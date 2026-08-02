"""
The scrobbler.

Core rule: whichever component is decoding is the only one that knows the
position, so that component owns the scrobble. On the Fire Stick that is always
Kodi. On the Mac, playercorefactory hands the stream to mpv and Kodi knows
nothing -- so Kodi writes a handoff file and stays out of the way.

Delegation is detected rather than configured. Probe evidence:

    native playback     getTotalTime() = 5087.926 / 1415.807   immediately
    mpv-delegated       getTotalTime() = 0.0                   on all 5 runs

Two clean populations, no overlap. Same code runs on both machines with no
per-device setting.
"""

import json
import os
import time

import xbmc
import xbmcaddon
import xbmcgui
import xbmcvfs

from . import identity as ident_mod
from .store import Store, log

ADDON = xbmcaddon.Addon()

# How long to wait for getTotalTime() before concluding Kodi is not decoding.
# Native populated it at t=0 in every probe run, so this is generous.
DELEGATION_TIMEOUT_S = 5.0
DELEGATION_POLL_S = 0.25

# Periodic save so a crash or a pulled plug does not lose the whole session.
HEARTBEAT_S = 60

# Do not seek if we are already essentially there.
SEEK_EPSILON_S = 10


def _profile_dir():
    path = xbmcvfs.translatePath(ADDON.getAddonInfo("profile"))
    if not xbmcvfs.exists(path):
        xbmcvfs.mkdirs(path)
    return path


def handoff_path():
    return os.path.join(_profile_dir(), "handoff.json")


def _setting_bool(key, default=False):
    try:
        return ADDON.getSettingBool(key)
    except Exception:
        return default


def _setting_int(key, default):
    try:
        return int(ADDON.getSettingInt(key))
    except Exception:
        return default


def notify(message, seconds=4000):
    if _setting_bool("show_notifications", True):
        xbmcgui.Dialog().notification("Scrobble", message,
                                      xbmcgui.NOTIFICATION_INFO, seconds)


class Scrobbler(xbmc.Player):
    def __init__(self, store):
        super(Scrobbler, self).__init__()
        self.store = store
        self.reset()

    def reset(self):
        self.identity = None
        self.duration = 0.0
        self.last_position = 0.0
        self.tracking = False
        self.delegated = False
        self.last_heartbeat = 0.0
        self.started_at = 0.0

    # ------------------------------------------------------------- helpers --

    def _detect_duration(self):
        """Poll for duration. Non-zero means Kodi is decoding."""
        deadline = time.time() + DELEGATION_TIMEOUT_S
        while time.time() < deadline:
            try:
                total = float(self.getTotalTime())
            except Exception:
                total = 0.0
            if total > 0:
                return total
            if xbmc.Monitor().waitForAbort(DELEGATION_POLL_S):
                break
            if not self.isPlaying():
                break
        return 0.0

    def _clear_handoff(self):
        """Kodi is decoding, so no external player will want this. Removing it
        stops a later standalone mpv launch picking up a stale identity."""
        try:
            if os.path.exists(handoff_path()):
                os.remove(handoff_path())
        except Exception as exc:
            log("could not clear handoff: {0}".format(exc), xbmc.LOGWARNING)

    def _write_handoff(self, identity):
        """
        Kodi has full identity milliseconds before it hands the URL to mpv.
        Persist it so the mpv script does not have to parse a filename --
        which is exactly why anime never worked on the old setup.

        The stream URL is the correlation key. Debrid URLs are unique per
        request, which has been a liability all along; here it is the feature.
        """
        try:
            url = self.getPlayingFile()
        except Exception:
            url = ""
        payload = {
            "identity": identity,
            "stream_url": url,
            "written_at": time.time(),
        }
        try:
            with open(handoff_path(), "w") as fh:
                json.dump(payload, fh)
            log("handoff written for {0}".format(ident_mod.describe(identity)))
        except Exception as exc:
            log("could not write handoff: {0}".format(exc), xbmc.LOGERROR)

    def _maybe_resume(self, identity):
        if not _setting_bool("auto_resume", True):
            return
        result = self.store.lookup(identity)
        if not result or not result.get("found"):
            return
        position = float(result.get("position_sec") or 0)
        if position <= SEEK_EPSILON_S:
            return
        if self.duration and position >= self.duration - SEEK_EPSILON_S:
            return

        # Rewind a few seconds so you get a run-up rather than landing mid-word.
        rewind = _setting_int("resume_rewind_sec", 0)
        target = max(0.0, position - rewind)

        if _setting_bool("ask_before_resume", False):
            hours, remainder = divmod(int(target), 3600)
            minutes, seconds = divmod(remainder, 60)
            stamp = "{0}:{1:02d}:{2:02d}".format(hours, minutes, seconds)
            if not xbmcgui.Dialog().yesno(
                    "Scrobble",
                    "Resume {0} from {1}?".format(
                        ident_mod.describe(identity), stamp),
                    nolabel="Start over", yeslabel="Resume"):
                log("resume declined by user")
                return

        try:
            self.seekTime(target)
            self.last_position = target
            mins, secs = divmod(int(target), 60)
            notify("Resuming at {0}:{1:02d}".format(mins, secs))
            log("resumed {0} at {1:.1f}s (saved {2:.1f}s, rewind {3}s)".format(
                ident_mod.describe(identity), target, position, rewind))
        except Exception as exc:
            log("seek failed: {0}".format(exc), xbmc.LOGERROR)

    def _mirror_to_kodi(self, watched, position=0.0):
        """
        Mirror into Kodi's own database so TMDbHelper's episode view shows the
        tick. Its watched flags come from Trakt with no hook for another
        source, but Kodi's local record for the plugin path is what actually
        draws the overlay.
        """
        if not _setting_bool("auto_kodi_sync", True) or not self.identity:
            return
        try:
            from . import kodisync
            kodisync.mark_one(
                self.identity.get("kind"), self.identity.get("tmdb_id"),
                self.identity.get("season"), self.identity.get("episode"),
                watched=watched, position=position,
                duration=self.duration or 0)
        except Exception as exc:
            log("kodi mirror failed: {0}".format(exc), xbmc.LOGWARNING)

    def _save(self, final=False):
        if not self.identity or self.delegated:
            return
        position = self.last_position
        if position <= 0:
            return
        threshold = _setting_int("min_progress_sec", 60)
        if position < threshold and not final:
            return
        self.store.save_progress(self.identity, position, self.duration or None)
        if final:
            watched = bool(self.duration and position / self.duration >= 0.9)
            self._mirror_to_kodi(watched, position)
        if final:
            log("saved {0} at {1:.1f}s of {2:.1f}s".format(
                ident_mod.describe(self.identity), position, self.duration))

    # -------------------------------------------------------------- events --

    def onPlayBackStarted(self):
        """
        Earliest hook Kodi offers -- fires before the stream is available, and
        therefore before an external player has had time to open it.

        The handoff must be written here rather than after delegation
        detection. That detection polls getTotalTime() for several seconds, by
        which point mpv has already launched, loaded the file and looked for a
        handoff that did not yet exist.

        Writing early and deleting later if Kodi turns out to be decoding is
        the correct order. It also covers the known Kodi bug where
        onAVStarted does not fire for external players on some builds.
        """
        try:
            identity = ident_mod.extract(self)
        except Exception as exc:
            log("early identity extraction failed: {0}".format(exc),
                xbmc.LOGWARNING)
            return
        if identity:
            self.identity = identity
            self._write_handoff(identity)

    def onAVStarted(self):
        previous = self.identity
        self.reset()

        identity = ident_mod.extract(self) or previous
        if identity is None:
            log("nothing identifiable playing -- ignoring", xbmc.LOGWARNING)
            self._clear_handoff()
            return
        self.identity = identity

        # Refresh the handoff -- metadata is sometimes richer by now than it
        # was at onPlayBackStarted, and rewriting is cheap.
        self._write_handoff(identity)

        self.duration = self._detect_duration()
        self.delegated = self.duration <= 0

        if self.delegated:
            log("Kodi is not decoding (external player) -- handed off, "
                "not scrobbling")
            return

        # Kodi is decoding after all, so the handoff was speculative.
        self._clear_handoff()

        log("scrobbling {0} ({1:.0f}s)".format(
            ident_mod.describe(identity), self.duration))
        self._maybe_resume(identity)
        self.tracking = True
        self.started_at = time.time()
        self.last_heartbeat = time.time()

    def onPlayBackSeek(self, time_ms, seek_offset):
        self._poll_position()

    def onPlayBackSeekChapter(self, chapter):
        self._poll_position()

    def _poll_position(self):
        if not self.tracking:
            return
        try:
            position = float(self.getTime())
        except Exception:
            return
        if position >= 0:
            self.last_position = position
        if not self.duration:
            try:
                self.duration = float(self.getTotalTime())
            except Exception:
                pass

    def tick(self):
        """Called from the service loop while playing."""
        if not self.tracking:
            return
        self._poll_position()
        if time.time() - self.last_heartbeat >= HEARTBEAT_S:
            self.last_heartbeat = time.time()
            self._save()

    def _finish(self, reason):
        if not self.tracking:
            self.reset()
            return
        log("playback {0} at {1:.1f}s".format(reason, self.last_position))
        self._save(final=True)
        self.reset()

    def onPlayBackStopped(self):
        self._finish("stopped")

    def onPlayBackEnded(self):
        # Reaching the end means watched, regardless of the last sampled
        # position -- getTime() is unreliable at this point.
        if self.tracking and self.identity:
            log("playback ended -- marking watched")
            if self.duration:
                self.last_position = self.duration
                self._save(final=True)
            else:
                self.store.mark_watched(self.identity)
        self.reset()

    def onPlayBackError(self):
        self._finish("errored")

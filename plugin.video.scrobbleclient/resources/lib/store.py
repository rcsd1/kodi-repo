"""
Store client.

Deliberately uses urllib from Kodi's bundled Python rather than
script.module.requests -- one less dependency to break on Android, which is
the platform you can least easily debug.

Writes that fail are queued to disk and replayed later. The Fire Stick's
network is not always up when playback ends.
"""

import json
import os
import time
import urllib.error
import urllib.parse
import urllib.request

import xbmc
import xbmcaddon
import xbmcvfs

ADDON = xbmcaddon.Addon()
ADDON_ID = ADDON.getAddonInfo("id")

TIMEOUT = 6  # short: a dead store must never stall playback
QUEUE_LIMIT = 500


def log(msg, level=xbmc.LOGINFO):
    """Debug-level lines are suppressed unless the setting is on, so a normal
    Kodi log is not flooded."""
    if level == xbmc.LOGDEBUG:
        try:
            if not xbmcaddon.Addon().getSettingBool("debug_logging"):
                return
        except Exception:
            return
    xbmc.log("[{0}] {1}".format(ADDON_ID, msg), level)


def _profile_dir():
    # translatePath is mandatory here. Raw paths work on macOS and fail on
    # Android.
    path = xbmcvfs.translatePath(ADDON.getAddonInfo("profile"))
    if not xbmcvfs.exists(path):
        xbmcvfs.mkdirs(path)
    return path


def _queue_path():
    return os.path.join(_profile_dir(), "queue.json")


def _seq_path():
    return os.path.join(_profile_dir(), "seq.txt")


def next_seq():
    """Monotonic per-device sequence. Makes queue replay idempotent server-side."""
    path = _seq_path()
    n = 0
    try:
        with open(path, "r") as fh:
            n = int(fh.read().strip() or 0)
    except Exception:
        n = int(time.time())  # first run: start somewhere unique
    n += 1
    try:
        with open(path, "w") as fh:
            fh.write(str(n))
    except Exception as exc:
        log("could not persist sequence: {0}".format(exc), xbmc.LOGWARNING)
    return n


class Store(object):
    def __init__(self):
        self.reload()

    last_error = None

    def reload(self):
        self.last_error = None
        addon = xbmcaddon.Addon()
        self.base = (addon.getSetting("store_url") or "").rstrip("/")
        self.token = addon.getSetting("device_token") or ""

    @property
    def configured(self):
        return bool(self.base and self.token)

    # ---------------------------------------------------------------- http --

    def _request(self, method, path, payload=None):
        url = self.base + path
        data = json.dumps(payload).encode("utf-8") if payload is not None else None
        req = urllib.request.Request(url, data=data, method=method)
        req.add_header("Authorization", "Bearer " + self.token)
        req.add_header("Content-Type", "application/json")
        with urllib.request.urlopen(req, timeout=TIMEOUT) as resp:
            body = resp.read().decode("utf-8")
            return json.loads(body) if body else {}

    def call(self, method, path, payload=None, queue_on_failure=False):
        if not self.configured:
            log("store not configured -- set URL and token in addon settings",
                xbmc.LOGWARNING)
            return None
        try:
            return self._request(method, path, payload)
        except urllib.error.HTTPError as exc:
            detail = ""
            try:
                detail = exc.read().decode("utf-8")[:300]
            except Exception:
                pass
            log("HTTP {0} on {1} {2}".format(exc.code, path, detail), xbmc.LOGERROR)
            # 503 from /recommendations means the server has no TMDB key.
            # Surfacing the real reason beats a generic "unavailable".
            self.last_error = {"code": exc.code, "detail": detail}
            # 4xx is our fault and requeueing would loop forever. Only queue on
            # transport failures and server errors.
            if queue_on_failure and exc.code >= 500:
                self.enqueue(method, path, payload)
            return None
        except Exception as exc:
            log("transport error on {0}: {1}".format(path, exc), xbmc.LOGWARNING)
            if queue_on_failure:
                self.enqueue(method, path, payload)
            return None

    # --------------------------------------------------------------- queue --

    def _read_queue(self):
        try:
            with open(_queue_path(), "r") as fh:
                return json.load(fh)
        except Exception:
            return []

    def _write_queue(self, items):
        try:
            with open(_queue_path(), "w") as fh:
                json.dump(items[-QUEUE_LIMIT:], fh)
        except Exception as exc:
            log("could not write queue: {0}".format(exc), xbmc.LOGERROR)

    def enqueue(self, method, path, payload):
        items = self._read_queue()
        items.append({"method": method, "path": path, "payload": payload,
                      "queued_at": time.time()})
        self._write_queue(items)
        log("queued {0} {1} ({2} pending)".format(method, path, len(items)))

    def flush(self):
        items = self._read_queue()
        if not items or not self.configured:
            return 0
        remaining, sent = [], 0
        for item in items:
            try:
                self._request(item["method"], item["path"], item.get("payload"))
                sent += 1
            except urllib.error.HTTPError as exc:
                if exc.code < 500:
                    sent += 1  # server rejected it permanently; stop retrying
                else:
                    remaining.append(item)
            except Exception:
                remaining.append(item)
                break  # network is down, stop hammering it
        self._write_queue(remaining)
        if sent:
            log("flushed {0} queued writes, {1} remaining".format(sent, len(remaining)))
        return sent

    # ------------------------------------------------------------ endpoints --

    def lookup(self, identity):
        return self.call("POST", "/progress/lookup", identity)

    def save_progress(self, identity, position, duration=None):
        payload = {"identity": identity, "position_sec": max(0.0, float(position)),
                   "client_seq": next_seq()}
        if duration:
            payload["duration_sec"] = float(duration)
        return self.call("POST", "/progress", payload, queue_on_failure=True)

    def mark_watched(self, identity):
        return self.call("POST", "/watched",
                         {"identity": identity, "client_seq": next_seq()},
                         queue_on_failure=True)

    def in_progress(self, limit=100, kind="", show=None):
        path = "/progress?limit={0}".format(int(limit))
        if show:
            path += "&show={0}".format(int(show))
        elif kind in ("movie", "episode"):
            path += "&kind=" + kind
        return self.call("GET", path)

    def watched(self, limit=100, kind="", show=None):
        path = "/watched?limit={0}".format(int(limit))
        if show:
            path += "&show={0}".format(int(show))
        elif kind in ("movie", "episode"):
            path += "&kind=" + kind
        return self.call("GET", path)

    def in_progress_shows(self, limit=100):
        return self.call("GET", "/progress/shows?limit={0}".format(int(limit)))

    def watched_shows(self, limit=100):
        return self.call("GET", "/watched/shows?limit={0}".format(int(limit)))

    def rate(self, identity, rating):
        return self.call("POST", "/ratings",
                         {"identity": identity, "rating": int(rating)},
                         queue_on_failure=True)

    def recommendations(self, media_type="movie", tier="any", genres="",
                        limit=60, languages="", exclude_genres="",
                        min_votes=0):
        query = "media_type={0}&tier={1}&limit={2}".format(
            urllib.parse.quote(media_type), urllib.parse.quote(tier), int(limit))
        if genres:
            query += "&genres=" + urllib.parse.quote(str(genres))
        if languages:
            query += "&languages=" + urllib.parse.quote(str(languages))
        if exclude_genres:
            query += "&exclude_genres=" + urllib.parse.quote(str(exclude_genres))
        if min_votes:
            query += "&min_votes={0}".format(int(min_votes))
        return self.call("GET", "/recommendations?" + query)

    def health(self):
        return self.call("GET", "/health")

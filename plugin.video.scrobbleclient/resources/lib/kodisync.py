"""
Push watched state into Kodi's own database.

TMDbHelper reads watched flags from Trakt, and there is no hook to point it
elsewhere -- so its episode view can never show this store's data directly.

But Kodi records playcount and resume locally for any file it plays, keyed on
the path. For a TMDbHelper item that path is its plugin:// play URL, which is
stable across playbacks. That local record is what draws the in-progress ring
you see on episodes even with Trakt broken.

So: write the same records ourselves, and the ticks appear in TMDbHelper's view
without touching TMDbHelper at all.

Whether Kodi accepts Files.SetFileDetails for plugin:// paths has been reported
both ways over the years, so `diagnose()` settles it on the actual device
before anything is built on top.
"""

import json

import xbmc
import xbmcaddon
import xbmcgui

from .store import Store, log

ADDON = xbmcaddon.Addon()

DEFAULT_PLAY_EPISODE = ("plugin://plugin.video.themoviedb.helper/?info=play"
                        "&tmdb_type=tv&tmdb_id={tmdb_id}"
                        "&season={season}&episode={episode}")
DEFAULT_PLAY_MOVIE = ("plugin://plugin.video.themoviedb.helper/?info=play"
                      "&tmdb_type=movie&tmdb_id={tmdb_id}")


def _setting(key, default=""):
    try:
        return ADDON.getSetting(key) or default
    except Exception:
        return default


def _(string_id):
    try:
        return ADDON.getLocalizedString(string_id) or ""
    except Exception:
        return ""


def jsonrpc(method, params=None):
    payload = {"jsonrpc": "2.0", "id": 1, "method": method}
    if params is not None:
        payload["params"] = params
    try:
        return json.loads(xbmc.executeJSONRPC(json.dumps(payload)))
    except Exception as exc:
        return {"error": {"message": "{0}: {1}".format(type(exc).__name__, exc)}}


def episode_path(tmdb_id, season, episode):
    template = _setting("play_url_episode", DEFAULT_PLAY_EPISODE)
    try:
        return template.format(tmdb_id=tmdb_id, season=season, episode=episode)
    except (KeyError, IndexError):
        return DEFAULT_PLAY_EPISODE.format(
            tmdb_id=tmdb_id, season=season, episode=episode)


def movie_path(tmdb_id):
    template = _setting("play_url_movie", DEFAULT_PLAY_MOVIE)
    try:
        return template.format(tmdb_id=tmdb_id)
    except (KeyError, IndexError):
        return DEFAULT_PLAY_MOVIE.format(tmdb_id=tmdb_id)


# TMDbHelper sets playcount on its own list items from Trakt, and a plugin's
# value wins over Kodi's local record -- confirmed by writing playcount=1 to a
# path that Files.GetDirectory then reported as playcount=0. So no write here
# can produce a green tick in its view.
#
# Resume is different. Kodi supplies it for plugin paths and TMDbHelper does
# not override it, which is why in-progress rings appear even with Trakt dead.
#
# So watched episodes can be written as a near-complete resume point instead:
# a nearly-full ring rather than a tick, but it does distinguish watched from
# unwatched at a glance, which is the actual need.
WATCHED_AS_RESUME_FRACTION = 0.995
ASSUMED_RUNTIME_SEC = 45 * 60


def auto_enabled() -> bool:
    try:
        return ADDON.getSettingBool("auto_kodi_sync")
    except Exception:
        return True


def watched_as_resume() -> bool:
    """
    Default on, because playcount does not survive.

    Files.SetFileDetails accepts playcount for a plugin path and then drops it
    -- reading the same path back reports 0 every time. Resume does persist.
    So a watched episode is written as a near-complete resume point, which is
    the only marker that survives the round trip.
    """
    try:
        return ADDON.getSettingBool("watched_as_resume")
    except Exception:
        return True


def set_watched(path, watched=True, position=0.0, duration=0.0):
    """
    Write playcount and resume for a path Kodi is not tracking as a library
    item. `media` must be "video" -- it is the only value the method accepts.
    """
    params = {"file": path, "media": "video",
              "playcount": 1 if watched else 0}

    if watched and watched_as_resume():
        total = float(duration) or float(ASSUMED_RUNTIME_SEC)
        params["resume"] = {"position": total * WATCHED_AS_RESUME_FRACTION,
                            "total": total}
    elif not watched and position and duration:
        params["resume"] = {"position": float(position),
                            "total": float(duration)}
    elif watched:
        params["resume"] = {"position": 0.0, "total": 0.0}

    return jsonrpc("Files.SetFileDetails", params)


def read_state(path):
    return jsonrpc("Files.GetFileDetails", {
        "file": path, "media": "video",
        "properties": ["playcount", "resume", "lastplayed"]})


# --------------------------------------------------------------------------
# diagnostic
# --------------------------------------------------------------------------

def diagnose():
    """
    Settle whether Kodi accepts these writes, on this device, before relying
    on them. Writes to a made-up path, reads it back, then clears it.
    """
    probe = ("plugin://plugin.video.themoviedb.helper/?info=play"
             "&tmdb_type=tv&tmdb_id=999999999&season=1&episode=1")

    lines = []
    write = set_watched(probe, watched=True)
    if "error" in write:
        lines.append("WRITE REJECTED")
        lines.append(str(write["error"].get("message", write["error"])))
        lines.append("")
        lines.append("Kodi will not accept playcount for plugin:// paths on "
                     "this build, so watched ticks cannot be pushed into "
                     "TMDbHelper's view. Use the built-in episode list "
                     "instead.")
        xbmcgui.Dialog().textviewer("Watched sync test", "\n".join(lines))
        return False

    lines.append("Write accepted.")
    back = read_state(probe)
    detail = (back.get("result") or {}).get("filedetails") or {}
    playcount = detail.get("playcount")
    lines.append("Read back: playcount={0}".format(playcount))

    ok = playcount == 1
    if ok:
        lines.append("")
        lines.append("Kodi is storing watched state for plugin paths.")
        lines.append("Press 'Sync watched state to Kodi' and TMDbHelper's")
        lines.append("episode view will show your ticks.")
    else:
        lines.append("")
        lines.append("The write was accepted but did not persist, so this")
        lines.append("route will not work. Use the built-in episode list.")

    set_watched(probe, watched=False)
    xbmcgui.Dialog().textviewer("Watched sync test", "\n".join(lines))
    return ok


def inspect_show(tmdb_id, season=1):
    """
    Ask TMDbHelper what paths its own episode items use.

    Guessing the play URL format is how the first sync wrote records nothing
    would ever read. Files.GetDirectory returns the real items, so the format
    comes from TMDbHelper rather than from assumption.
    """
    listing = jsonrpc("Files.GetDirectory", {
        "directory": ("plugin://plugin.video.themoviedb.helper/?info=episodes"
                      "&tmdb_type=tv&tmdb_id={0}&season={1}".format(
                          tmdb_id, season)),
        "media": "video",
        "properties": ["title", "season", "episode", "playcount", "resume",
                       "file"],
    })
    if "error" in listing:
        return None, listing["error"]
    result = listing.get("result")
    # A malformed or unexpected reply must not take the sync down with it.
    if not isinstance(result, dict):
        return [], None
    files = result.get("files")
    return (files if isinstance(files, list) else []), None


def diagnose_paths(tmdb_id=None, season=1):
    """
    Show what Kodi holds for a chosen show, beside what was written.

    Reports resume as well as playcount: a written resume point that reads back
    as zero means the write is not landing where the listing reads, whereas one
    that reads back correctly but draws no ring means the skin is not using it.
    Those need opposite fixes.
    """
    store = Store()

    if not tmdb_id:
        shows = (store.in_progress_shows(20) or {}).get("items", [])
        watched = (store.watched_shows(20) or {}).get("items", [])
        by_id = {}
        for entry in shows + watched:
            by_id.setdefault(entry["tmdb_id"], entry)
        options = list(by_id.values())
        if not options:
            xbmcgui.Dialog().ok("Scrobble", "Nothing watched yet to inspect.")
            return
        labels = ["{0}  (tmdb {1})".format(o.get("title") or "?", o["tmdb_id"])
                  for o in options]
        choice = xbmcgui.Dialog().select("Which show?", labels)
        if choice < 0:
            return
        tmdb_id = options[choice]["tmdb_id"]
        season = options[choice].get("next_season") or 1

    files, error = inspect_show(tmdb_id, season)
    lines = ["Show {0}, season {1}".format(tmdb_id, season), ""]

    if error:
        lines.append("TMDbHelper listing failed:")
        lines.append(str(error.get("message", error)))
        xbmcgui.Dialog().textviewer("Episode state", "\n".join(lines))
        return

    if not files:
        lines.append("TMDbHelper returned no items for that season.")
        xbmcgui.Dialog().textviewer("Episode state", "\n".join(lines))
        return

    lines.append("Path this addon writes:")
    lines.append("  " + episode_path(tmdb_id, season, 1))
    lines.append("")
    lines.append("What Kodi reports for TMDbHelper's items:")
    lines.append("")

    mismatch = False
    for item in files[:6]:
        number = item.get("episode")
        resume = item.get("resume") or {}
        lines.append("  S{0}E{1}  playcount={2}  resume={3}/{4}".format(
            item.get("season"), number, item.get("playcount"),
            int(resume.get("position") or 0), int(resume.get("total") or 0)))
        if number and item.get("file") != episode_path(tmdb_id, season, number):
            mismatch = True
            lines.append("    path differs:")
            lines.append("    " + str(item.get("file")))

    lines.append("")
    if mismatch:
        lines.append("PATH MISMATCH -- writes are going somewhere else.")
    else:
        lines.append("Paths match.")
        lines.append("")
        lines.append("If playcount and resume are zero here, Kodi is not "
                     "returning what was written and this route is dead. "
                     "If they are correct but nothing is drawn, the skin is "
                     "ignoring them.")

    # Also read the same path directly, which bypasses TMDbHelper entirely.
    direct = read_state(episode_path(tmdb_id, season, 1))
    detail = (direct.get("result") or {}).get("filedetails") or {}
    lines.append("")
    lines.append("Reading the path directly (no TMDbHelper):")
    lines.append("  playcount={0}  resume={1}".format(
        detail.get("playcount"), detail.get("resume")))

    xbmcgui.Dialog().textviewer("Episode state", "\n".join(lines))


# --------------------------------------------------------------------------
# the sync
# --------------------------------------------------------------------------

def sync(limit=5000, show_progress=True):
    """
    Push every watched item, and every resume point, into Kodi's database.

    Watched entries are written first because they are what is missing; resume
    points already appear, but writing them keeps the two in step when playback
    happened on another device.
    """
    store = Store()
    if not store.configured:
        xbmcgui.Dialog().ok("Scrobble", _(30070))
        return {"watched": 0, "progress": 0, "failed": 0}

    dialog = xbmcgui.DialogProgressBG() if show_progress else None
    if dialog:
        dialog.create("Scrobble", _(30098))

    # "failed" only counted rejected writes. Items skipped before the write --
    # no tmdb id, no season/episode -- were invisible, so a run that never
    # touched an item reported 0 failed and looked like a success.
    counts = {"watched": 0, "progress": 0, "failed": 0,
              "skipped_no_id": 0, "skipped_no_episode": 0}
    # Cache of real paths per (show, season), discovered from TMDbHelper rather
    # than assumed. Falls back to the template when a listing is unavailable.
    discovered = {}

    def path_for_episode(tmdb_id, season, episode):
        key = (tmdb_id, season)
        if key not in discovered:
            files, error = inspect_show(tmdb_id, season)
            mapping = {}
            for item in files or []:
                if item.get("episode") is not None and item.get("file"):
                    mapping[item["episode"]] = item["file"]
            discovered[key] = mapping
        return (discovered[key].get(episode)
                or episode_path(tmdb_id, season, episode))

    try:
        watched = (store.watched(limit=limit) or {}).get("items", [])
        total = max(1, len(watched))
        for index, item in enumerate(watched):
            if not item.get("tmdb_id"):
                counts["skipped_no_id"] += 1
                log("skipped (no tmdb id): {0}".format(
                    item.get("show_title") or item.get("title")))
                continue
            if item["kind"] == "episode":
                if item.get("season") is None or item.get("episode") is None:
                    counts["skipped_no_episode"] += 1
                    log("skipped (no season/episode): {0}".format(
                        item.get("show_title")))
                    continue
                path = path_for_episode(item["tmdb_id"], item["season"],
                                        item["episode"])
            else:
                path = movie_path(item["tmdb_id"])
            result = set_watched(path, watched=True,
                                 duration=item.get("duration_sec") or 0)
            if "error" in result:
                counts["failed"] += 1
            else:
                counts["watched"] += 1
            if dialog and index % 25 == 0:
                dialog.update(int(90.0 * index / total))

        for item in (store.in_progress(limit=limit) or {}).get("items", []):
            if not item.get("tmdb_id"):
                counts["skipped_no_id"] += 1
                log("progress skipped (no tmdb id): {0}".format(
                    item.get("show_title") or item.get("title")))
                continue
            if not item.get("duration_sec"):
                # No duration means no percentage, so no ring can be drawn.
                counts["skipped_no_episode"] += 1
                log("progress skipped (no duration): {0}".format(
                    item.get("show_title") or item.get("title")))
                continue
            if item["kind"] == "episode":
                if item.get("season") is None or item.get("episode") is None:
                    counts["skipped_no_episode"] += 1
                    log("progress skipped (no season/episode): {0}".format(
                        item.get("show_title")))
                    continue
                path = path_for_episode(item["tmdb_id"], item["season"],
                                        item["episode"])
            else:
                path = movie_path(item["tmdb_id"])
            result = set_watched(path, watched=False,
                                 position=item.get("position_sec") or 0,
                                 duration=item["duration_sec"])
            if "error" in result:
                counts["failed"] += 1
            else:
                counts["progress"] += 1
    finally:
        if dialog:
            dialog.close()

    log("kodi sync: {0}".format(counts))
    skipped = counts["skipped_no_id"] + counts["skipped_no_episode"]
    xbmcgui.Dialog().ok(
        "Scrobble",
        "Written to Kodi:\n"
        "   {0} watched\n"
        "   {1} in progress\n\n"
        "Skipped:\n"
        "   {2} with no TMDB id\n"
        "   {3} with no season/episode or duration\n\n"
        "Rejected by Kodi: {4}\n\n"
        "Skipped items cannot be written -- there is no TMDbHelper path to "
        "key them on. The Kodi log names each one.".format(
            counts["watched"], counts["progress"],
            counts["skipped_no_id"], counts["skipped_no_episode"],
            counts["failed"]))
    return counts


def clear_tmdbhelper_cache():
    """
    Deliberately does nothing.

    An earlier version deleted every .db, .sqlite and .cache file from
    TMDbHelper's addon_data folder to force its episode lists to rebuild. That
    was reckless -- those files are not all caches, and removing them left
    TMDbHelper unable to open a show at all.

    Nothing here writes to another addon's data. If a listing looks stale, use
    TMDbHelper's own cache controls or restart Kodi.
    """
    return {"removed": 0,
            "note": "cache clearing removed -- it broke TMDbHelper"}


# --------------------------------------------------------------------------
# incremental sync
# --------------------------------------------------------------------------

def _state_file():
    import os
    import xbmcvfs
    profile = xbmcvfs.translatePath(ADDON.getAddonInfo("profile"))
    if not xbmcvfs.exists(profile):
        xbmcvfs.mkdirs(profile)
    return os.path.join(profile, "kodisync.json")


def _load_seen():
    import os
    path = _state_file()
    if not os.path.isfile(path):
        return set()
    try:
        with open(path) as fh:
            return set(json.load(fh).get("seen") or [])
    except Exception:
        return set()


def _save_seen(seen):
    try:
        with open(_state_file(), "w") as fh:
            json.dump({"seen": sorted(seen)[-8000:]}, fh)
    except Exception as exc:
        log("could not persist sync state: {0}".format(exc), xbmc.LOGWARNING)


def _key(item):
    if item.get("kind") == "episode":
        return "e:{0}:{1}:{2}:{3}".format(
            item.get("tmdb_id"), item.get("season"), item.get("episode"),
            item.get("watched_at") or item.get("updated_at") or "")
    return "m:{0}:{1}".format(
        item.get("tmdb_id"), item.get("watched_at")
        or item.get("updated_at") or "")


def sync_recent(limit=200):
    """
    Pull whatever is new in the store and write it into Kodi.

    Playback on the Mac goes store-side only -- mpv has no way to touch this
    device's database, and when Kodi delegates to an external player its own
    scrobbler deliberately stays out of the way. Without this, anything watched
    elsewhere never gets a marker here.

    Keyed on a per-item fingerprint so repeat passes are cheap.
    """
    store = Store()
    if not store.configured:
        return {"written": 0}

    seen = _load_seen()
    written = 0

    for item in (store.watched(limit=limit) or {}).get("items", []):
        key = _key(item)
        if key in seen or not item.get("tmdb_id"):
            continue
        if item["kind"] == "episode" and (
                item.get("season") is None or item.get("episode") is None):
            continue
        path = (episode_path(item["tmdb_id"], item["season"], item["episode"])
                if item["kind"] == "episode" else movie_path(item["tmdb_id"]))
        if "error" not in set_watched(path, watched=True):
            seen.add(key)
            written += 1

    for item in (store.in_progress(limit=limit) or {}).get("items", []):
        key = _key(item)
        if key in seen or not item.get("tmdb_id") or not item.get("duration_sec"):
            continue
        if item["kind"] == "episode" and (
                item.get("season") is None or item.get("episode") is None):
            continue
        path = (episode_path(item["tmdb_id"], item["season"], item["episode"])
                if item["kind"] == "episode" else movie_path(item["tmdb_id"]))
        if "error" not in set_watched(path, watched=False,
                                      position=item.get("position_sec") or 0,
                                      duration=item["duration_sec"]):
            seen.add(key)
            written += 1

    if written:
        _save_seen(seen)
        log("incremental kodi sync wrote {0} records".format(written))
    return {"written": written}


def mark_one(kind, tmdb_id, season=None, episode=None, watched=True,
             position=0.0, duration=0.0):
    """Called from the scrobbler so live playback keeps Kodi in step without
    waiting for a full sync."""
    if not tmdb_id:
        return
    if kind == "episode":
        if season is None or episode is None:
            return
        path = episode_path(tmdb_id, season, episode)
    else:
        path = movie_path(tmdb_id)
    set_watched(path, watched=watched, position=position, duration=duration)

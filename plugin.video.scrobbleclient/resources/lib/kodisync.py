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


def set_watched(path, watched=True, position=0.0, duration=0.0):
    """
    Write playcount and resume for a path Kodi is not tracking as a library
    item. `media` must be "video" -- it is the only value the method accepts.
    """
    params = {"file": path, "media": "video",
              "playcount": 1 if watched else 0}
    if not watched and position and duration:
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
    """Show the real item paths beside the one this addon writes."""
    if not tmdb_id:
        store = Store()
        shows = (store.in_progress_shows(5) or {}).get("items", [])
        if not shows:
            shows = (store.watched_shows(5) or {}).get("items", [])
        if not shows:
            xbmcgui.Dialog().ok("Scrobble", "Nothing watched yet to inspect.")
            return
        tmdb_id = shows[0]["tmdb_id"]
        season = shows[0].get("next_season") or 1

    files, error = inspect_show(tmdb_id, season)
    lines = ["Show {0}, season {1}".format(tmdb_id, season), ""]

    if error:
        lines.append("TMDbHelper listing failed:")
        lines.append(str(error.get("message", error)))
        xbmcgui.Dialog().textviewer("Episode paths", "\n".join(lines))
        return

    if not files:
        lines.append("TMDbHelper returned no items for that season.")
        xbmcgui.Dialog().textviewer("Episode paths", "\n".join(lines))
        return

    lines.append("What this addon writes:")
    lines.append("  " + episode_path(tmdb_id, season, 1))
    lines.append("")
    lines.append("What TMDbHelper actually uses:")
    for item in files[:4]:
        lines.append("  S{0}E{1}  playcount={2}".format(
            item.get("season"), item.get("episode"), item.get("playcount")))
        lines.append("  " + str(item.get("file")))
        lines.append("")

    if files and files[0].get("file"):
        same = files[0]["file"] == episode_path(
            tmdb_id, season, files[0].get("episode") or 1)
        lines.append("MATCH" if same else "MISMATCH -- this is why no ticks")

    xbmcgui.Dialog().textviewer("Episode paths", "\n".join(lines))


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

    counts = {"watched": 0, "progress": 0, "failed": 0}
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
                continue
            if item["kind"] == "episode":
                if item.get("season") is None or item.get("episode") is None:
                    continue
                path = path_for_episode(item["tmdb_id"], item["season"],
                                        item["episode"])
            else:
                path = movie_path(item["tmdb_id"])
            result = set_watched(path, watched=True)
            if "error" in result:
                counts["failed"] += 1
            else:
                counts["watched"] += 1
            if dialog and index % 25 == 0:
                dialog.update(int(90.0 * index / total))

        for item in (store.in_progress(limit=limit) or {}).get("items", []):
            if not item.get("tmdb_id") or not item.get("duration_sec"):
                continue
            if item["kind"] == "episode":
                if item.get("season") is None or item.get("episode") is None:
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
    xbmcgui.Dialog().notification(
        "Scrobble",
        "{0} watched, {1} in progress, {2} failed".format(
            counts["watched"], counts["progress"], counts["failed"]),
        xbmcgui.NOTIFICATION_INFO, 5000)
    return counts


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

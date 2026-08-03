"""
Build a real Kodi TV library from the store.

Why this exists: Kodi accepts playcount for a plugin:// path and then drops it,
and resume points written the same way are not rendered by TMDbHelper either.
Both were verified on the device rather than assumed. So there is no way to
mark watched state on items that exist only as plugin paths.

Library items are different. An episode scanned into Kodi's video library has a
real DBID, and VideoLibrary.SetEpisodeDetails persists playcount against it --
the same mechanism Kodi uses for its own watched flags. Those flags render
everywhere, including in TMDbHelper, which picks up the DBID for items it finds
in the library.

The trade is a folder of .strm files, one per episode. Each contains a
TMDbHelper play URL, so playing a library item still goes through the same
players as before.

    Shows/
      Anne with an E (2017)/
        tvshow.nfo
        Season 01/
          Anne with an E S01E01.strm
"""

import json
import os
import re
import time

import xbmc
import xbmcaddon
import xbmcgui
import xbmcvfs

from .store import Store, log

ADDON = xbmcaddon.Addon()

PLAY_URL = ("plugin://plugin.video.themoviedb.helper/?info=play"
            "&tmdb_type=tv&tmdb_id={tmdb_id}&season={season}&episode={episode}")

# Windows-hostile characters plus the ones Kodi's scanner dislikes.
UNSAFE = re.compile(r'[<>:"/\\|?*\x00-\x1f]')


def _(string_id):
    try:
        return ADDON.getLocalizedString(string_id) or ""
    except Exception:
        return ""


def _setting(key, default=""):
    try:
        return ADDON.getSetting(key) or default
    except Exception:
        return default


def auto_enabled():
    try:
        return ADDON.getSettingBool("auto_library_sync")
    except Exception:
        return True


def library_path():
    """Where the .strm tree lives. Defaults inside the addon's own profile so
    it works with no configuration, but any path can be used."""
    configured = _setting("library_path", "")
    if configured:
        return xbmcvfs.translatePath(configured)
    return os.path.join(
        xbmcvfs.translatePath(ADDON.getAddonInfo("profile")), "library")


def safe_name(text):
    cleaned = UNSAFE.sub("", (text or "Unknown")).strip().rstrip(".")
    return cleaned or "Unknown"


def jsonrpc(method, params=None):
    payload = {"jsonrpc": "2.0", "id": 1, "method": method}
    if params is not None:
        payload["params"] = params
    try:
        return json.loads(xbmc.executeJSONRPC(json.dumps(payload)))
    except Exception as exc:
        return {"error": {"message": "{0}: {1}".format(type(exc).__name__, exc)}}


# --------------------------------------------------------------------------
# building
# --------------------------------------------------------------------------

def write_show(root, show, episodes):
    """
    One show: a folder, an nfo naming its TMDB id, and a .strm per episode.

    The nfo matters -- without it Kodi scrapes by folder name and can match the
    wrong series, which would attach watched state to the wrong thing.
    """
    title = safe_name(show.get("title"))
    year = show.get("year")
    folder = "{0} ({1})".format(title, year) if year else title
    show_dir = os.path.join(root, folder)

    if not xbmcvfs.exists(show_dir):
        xbmcvfs.mkdirs(show_dir)

    nfo = os.path.join(show_dir, "tvshow.nfo")
    if not os.path.exists(nfo):
        with open(nfo, "w", encoding="utf-8") as fh:
            fh.write(
                '<?xml version="1.0" encoding="UTF-8"?>\n'
                "<tvshow>\n"
                "    <title>{0}</title>\n"
                "    <uniqueid type=\"tmdb\" default=\"true\">{1}</uniqueid>\n"
                "</tvshow>\n"
                "https://www.themoviedb.org/tv/{1}\n".format(
                    show.get("title") or title, show["tmdb_id"]))

    written = 0
    for episode in episodes:
        season = episode.get("season")
        number = episode.get("episode")
        if season is None or number is None or season == 0:
            continue

        season_dir = os.path.join(show_dir, "Season {0:02d}".format(season))
        if not xbmcvfs.exists(season_dir):
            xbmcvfs.mkdirs(season_dir)

        name = "{0} S{1:02d}E{2:02d}.strm".format(title, season, number)
        path = os.path.join(season_dir, name)
        if os.path.exists(path):
            continue

        with open(path, "w", encoding="utf-8") as fh:
            fh.write(PLAY_URL.format(tmdb_id=show["tmdb_id"],
                                     season=season, episode=number))
        written += 1

    return written


def build(progress_dialog=True):
    """Generate the tree for every show the store knows about."""
    store = Store()
    if not store.configured:
        xbmcgui.Dialog().ok("Scrobble", _(30070))
        return {"shows": 0, "episodes": 0}

    root = library_path()
    if not xbmcvfs.exists(root):
        xbmcvfs.mkdirs(root)

    shows = {}
    for source in (store.in_progress_shows(500), store.watched_shows(500)):
        for entry in (source or {}).get("items", []):
            if entry.get("tmdb_id"):
                shows.setdefault(entry["tmdb_id"], entry)

    if not shows:
        xbmcgui.Dialog().ok("Scrobble", "No shows in the store yet.")
        return {"shows": 0, "episodes": 0}

    dialog = xbmcgui.DialogProgressBG() if progress_dialog else None
    if dialog:
        dialog.create("Scrobble", _(30110))

    counts = {"shows": 0, "episodes": 0, "no_episodes": 0}
    try:
        for index, show in enumerate(shows.values()):
            data = store.show_episodes(show["tmdb_id"])
            episodes = (data or {}).get("items", [])
            if not episodes:
                # The store caches episode lists lazily; a show it has not
                # fetched yet simply gets skipped and picked up next run.
                counts["no_episodes"] += 1
                continue
            counts["episodes"] += write_show(root, show, episodes)
            counts["shows"] += 1
            if dialog:
                dialog.update(int(100.0 * index / max(1, len(shows))),
                              message=show.get("title") or "")
    finally:
        if dialog:
            dialog.close()

    log("library build: {0}".format(counts))
    return counts


# --------------------------------------------------------------------------
# watched state, the part that actually persists
# --------------------------------------------------------------------------

def library_episodes():
    """Every episode Kodi has scanned, keyed by (tmdb id, season, episode)."""
    shows = jsonrpc("VideoLibrary.GetTVShows",
                    {"properties": ["uniqueid", "title"]})
    result = (shows.get("result") or {}).get("tvshows") or []

    by_tmdb = {}
    for show in result:
        unique = show.get("uniqueid") or {}
        tmdb = unique.get("tmdb")
        if tmdb:
            try:
                by_tmdb[int(tmdb)] = show["tvshowid"]
            except (TypeError, ValueError):
                continue

    episodes = {}
    for tmdb_id, tvshowid in by_tmdb.items():
        listing = jsonrpc("VideoLibrary.GetEpisodes", {
            "tvshowid": tvshowid,
            "properties": ["season", "episode", "playcount"]})
        for item in (listing.get("result") or {}).get("episodes") or []:
            episodes[(tmdb_id, item.get("season"), item.get("episode"))] = item
    return episodes


def sync_watched(progress_dialog=True):
    """
    Push watched state onto library episodes.

    Unlike the plugin-path route, this persists: VideoLibrary.SetEpisodeDetails
    writes against a real DBID and Kodi renders the flag itself.
    """
    store = Store()
    if not store.configured:
        xbmcgui.Dialog().ok("Scrobble", _(30070))
        return {"marked": 0}

    known = library_episodes()
    if not known:
        # Silent when running unattended -- an empty library just means the
        # user has not set the source up, which is not an error every ten
        # minutes.
        if progress_dialog:
            xbmcgui.Dialog().ok(
                "Scrobble",
                "No scanned episodes found.\n\n"
                "Add the library folder as a TV Shows source and scan it "
                "first.")
        return {"marked": 0, "resumed": 0, "not_in_library": 0}

    dialog = xbmcgui.DialogProgressBG() if progress_dialog else None
    if dialog:
        dialog.create("Scrobble", _(30111))

    counts = {"marked": 0, "resumed": 0, "not_in_library": 0}
    try:
        for item in (store.watched(limit=5000) or {}).get("items", []):
            if item.get("kind") != "episode":
                continue
            key = (item.get("tmdb_id"), item.get("season"),
                   item.get("episode"))
            entry = known.get(key)
            if not entry:
                counts["not_in_library"] += 1
                continue
            if entry.get("playcount"):
                continue
            reply = jsonrpc("VideoLibrary.SetEpisodeDetails", {
                "episodeid": entry["episodeid"], "playcount": 1})
            if "error" not in reply:
                counts["marked"] += 1

        for item in (store.in_progress(limit=500) or {}).get("items", []):
            if item.get("kind") != "episode" or not item.get("duration_sec"):
                continue
            key = (item.get("tmdb_id"), item.get("season"),
                   item.get("episode"))
            entry = known.get(key)
            if not entry:
                continue
            reply = jsonrpc("VideoLibrary.SetEpisodeDetails", {
                "episodeid": entry["episodeid"],
                "resume": {"position": float(item.get("position_sec") or 0),
                           "total": float(item["duration_sec"])}})
            if "error" not in reply:
                counts["resumed"] += 1
    finally:
        if dialog:
            dialog.close()

    log("library watched sync: {0}".format(counts))
    return counts


def scan(show_dialogs=True):
    """Ask Kodi to scan the library folder."""
    jsonrpc("VideoLibrary.Scan", {"directory": library_path(),
                                  "showdialogs": show_dialogs})


def maintain():
    """
    Keep the library current without anyone pressing anything.

    Building is cheap when nothing changed -- existing files are skipped -- so
    this can run on the same timer as the marking pass. A new show, or new
    episodes of one already there, get files written and a scan triggered;
    otherwise nothing happens.

    Requiring a button press after starting a new show would be a poor trade
    for the whole point of this, which is that watched state looks after
    itself.
    """
    if not auto_enabled():
        return {"built": 0, "marked": 0}

    # Nothing to maintain until the library source has been set up.
    if not library_episodes():
        return {"built": 0, "marked": 0, "note": "library not scanned yet"}

    built = build(progress_dialog=False)
    if built.get("episodes"):
        log("library gained {0} episode files -- scanning".format(
            built["episodes"]))
        scan(show_dialogs=False)
        # The scan is asynchronous; marking happens on the next pass once
        # Kodi has the new episodes.
        return {"built": built["episodes"], "marked": 0}

    marked = sync_watched(progress_dialog=False)
    return {"built": 0, "marked": marked.get("marked", 0),
            "resumed": marked.get("resumed", 0)}

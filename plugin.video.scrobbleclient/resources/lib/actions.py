"""
Settings actions and the store status screen.

Everything reachable from Settings without a terminal: test the connection,
change the watched threshold, force a maintenance pass, inspect the store.

Actions that touch admin endpoints check for an admin token first and say so
plainly rather than failing with a bare 403.
"""

import json
import os

import xbmc
import xbmcaddon
import xbmcgui
import xbmcvfs

from .store import Store, log

ADDON = xbmcaddon.Addon()


def _(string_id):
    """Localised string. Every visible string lives in strings.po."""
    return ADDON.getLocalizedString(string_id)


def notify(message, icon=xbmcgui.NOTIFICATION_INFO, ms=4000):
    xbmcgui.Dialog().notification("Scrobble", message, icon, ms)


def _admin_token():
    try:
        return (ADDON.getSetting("admin_token") or "").strip()
    except Exception:
        return ""


def _require_admin():
    if not _admin_token():
        xbmcgui.Dialog().ok("Scrobble", _(30077))
        return False
    return True


def _admin_call(method, path, payload=None):
    """
    Admin endpoints use the admin token rather than the device token, so this
    bypasses the normal Store client rather than storing two tokens in it.
    """
    import urllib.error
    import urllib.request

    store = Store()
    if not store.configured:
        notify(_(30070), xbmcgui.NOTIFICATION_ERROR)
        return None

    url = store.base + path
    data = json.dumps(payload).encode("utf-8") if payload is not None else None
    req = urllib.request.Request(url, data=data, method=method)
    req.add_header("Authorization", "Bearer " + _admin_token())
    req.add_header("Content-Type", "application/json")
    try:
        with urllib.request.urlopen(req, timeout=30) as resp:
            body = resp.read().decode("utf-8")
            return json.loads(body) if body else {}
    except urllib.error.HTTPError as exc:
        if exc.code in (401, 403):
            xbmcgui.Dialog().ok("Scrobble", _(30077))
        else:
            notify("HTTP {0}".format(exc.code), xbmcgui.NOTIFICATION_ERROR)
    except Exception as exc:
        log("admin call failed: {0}".format(exc), xbmc.LOGERROR)
        notify(_(30071), xbmcgui.NOTIFICATION_ERROR)
    return None


# --------------------------------------------------------------------------
# actions
# --------------------------------------------------------------------------

def test_connection():
    store = Store()
    if not store.configured:
        xbmcgui.Dialog().ok("Scrobble", _(30070))
        return

    health = store.health()
    if not health:
        xbmcgui.Dialog().ok("Scrobble", "{0}\n\n{1}".format(_(30071), store.base))
        return

    progress = store.in_progress(limit=1)
    if progress is None:
        xbmcgui.Dialog().ok(
            "Scrobble",
            "Store reached, but the device token was rejected.\n\n"
            "Check the token for THIS device.")
        return

    xbmcgui.Dialog().ok(
        "Scrobble",
        "{0}\n\n{1}\nTitles known to the store: {2}".format(
            _(30072), store.base, health.get("media_count", "?")))


def apply_threshold():
    if not _require_admin():
        return
    try:
        percent = int(ADDON.getSettingInt("watched_threshold"))
    except Exception:
        percent = 90
    result = _admin_call("PUT", "/admin/config",
                         {"watched_threshold": percent / 100.0})
    if result is not None:
        notify("{0} - {1}%".format(_(30078), percent))


def show_stats():
    if not _require_admin():
        return
    stats = _admin_call("GET", "/stats")
    if stats is None:
        return

    lines = [
        "Movies: {0}".format(stats.get("movies", 0)),
        "Episodes: {0}".format(stats.get("episodes", 0)),
        "In progress: {0}".format(stats.get("in_progress", 0)),
        "Plays recorded: {0}".format(stats.get("watched_plays", 0)),
        "Devices: {0}".format(stats.get("devices", 0)),
        "",
        "Anime series mapped: {0}".format(stats.get("anime_series_mapped", 0)),
        "Anime episodes cached: {0}".format(stats.get("anime_episodes_cached", 0)),
        "Unmapped anime: {0}".format(stats.get("unmapped_anime", 0)),
        "Awaiting TMDB backfill: {0}".format(stats.get("needs_tmdb_backfill", 0)),
        "TMDB key set: {0}".format("yes" if stats.get("tmdb_key_set") else "NO"),
    ]
    xbmcgui.Dialog().textviewer("Store status", "\n".join(lines))


def refresh_recs():
    store = Store()
    if not store.configured:
        notify(_(30070), xbmcgui.NOTIFICATION_ERROR)
        return
    progress = xbmcgui.DialogProgressBG()
    progress.create("Scrobble", _(30042))
    try:
        done = 0
        for media_type in ("movie", "tv"):
            result = store.call(
                "GET", "/recommendations?media_type={0}&refresh=true&limit=1".format(
                    media_type))
            if result is not None:
                done += 1
            progress.update(50 * done)
    finally:
        progress.close()
    notify(_(30078) if done else _(30079),
           xbmcgui.NOTIFICATION_INFO if done else xbmcgui.NOTIFICATION_ERROR)


def anime_ingest():
    if not _require_admin():
        return
    progress = xbmcgui.DialogProgressBG()
    progress.create("Scrobble", _(30043))
    try:
        result = _admin_call("POST", "/admin/anime/ingest")
    finally:
        progress.close()
    if result is not None:
        notify("Mapped {0} series, fixed {1}".format(
            result.get("series_mapped", 0), result.get("backfilled", 0)))


def backfill():
    if not _require_admin():
        return
    progress = xbmcgui.DialogProgressBG()
    progress.create("Scrobble", _(30044))
    try:
        result = _admin_call("POST", "/admin/backfill")
    finally:
        progress.close()
    if result is not None:
        notify("Resolved {0}, merged {1}, {2} left".format(
            result.get("resolved", 0), result.get("merged", 0),
            result.get("pending", 0)))


def clear_queue():
    path = os.path.join(
        xbmcvfs.translatePath(ADDON.getAddonInfo("profile")), "queue.json")
    try:
        pending = 0
        if os.path.exists(path):
            with open(path) as fh:
                pending = len(json.load(fh))
        if pending and not xbmcgui.Dialog().yesno(
                "Scrobble",
                "Discard {0} queued writes?\n\nThey have not reached the store "
                "yet and will be lost.".format(pending)):
            return
        if os.path.exists(path):
            os.remove(path)
        notify("{0} - {1} discarded".format(_(30078), pending))
    except Exception as exc:
        log("could not clear queue: {0}".format(exc), xbmc.LOGERROR)
        notify(_(30079), xbmcgui.NOTIFICATION_ERROR)


def test_kodi_sync():
    from . import kodisync
    kodisync.diagnose()


def inspect_paths():
    from . import kodisync
    kodisync.diagnose_paths()


def kodi_sync():
    from . import kodisync
    kodisync.sync()


ACTIONS = {
    "test": test_connection,
    "test_kodi_sync": test_kodi_sync,
    "kodi_sync": kodi_sync,
    "inspect_paths": inspect_paths,
    "apply_threshold": apply_threshold,
    "stats": show_stats,
    "refresh_recs": refresh_recs,
    "anime_ingest": anime_ingest,
    "backfill": backfill,
    "clear_queue": clear_queue,
}


def run(name):
    handler = ACTIONS.get(name)
    if handler is None:
        log("unknown action: {0}".format(name), xbmc.LOGWARNING)
        return False
    handler()
    return True

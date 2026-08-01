"""
The Resume Watching listing.

Draws exactly what TMDbHelper's Trakt in-progress widget drew, from your own
store instead. The progress ring is a standard ListItem resume point -- the
probe confirmed it renders correctly in an Arctic Horizon 2 widget, so Kodi
issue #25045 does not affect this setup.
"""

import sys
import urllib.parse

import xbmc
import xbmcaddon
import xbmcgui
import xbmcplugin

from .store import Store, log

ADDON = xbmcaddon.Addon()

ART_MAP = (
    ("poster", ("poster", "show_poster", "thumb")),
    ("thumb", ("thumb", "poster", "show_poster")),
    ("fanart", ("fanart", "show_fanart")),
    ("landscape", ("landscape", "fanart")),
    ("clearlogo", ("clearlogo", "show_clearlogo")),
)

FALLBACK_ART = "DefaultVideo.png"


def _(string_id):
    """Localised string. Kodi ignores plain text labels in the v1 settings
    format, so every visible string lives in strings.po."""
    try:
        return ADDON.getLocalizedString(string_id) or ""
    except Exception:
        return ""


def _setting(key, default=""):
    try:
        return ADDON.getSetting(key) or default
    except Exception:
        return default


def _play_url(item):
    """
    What clicking an item does. Defaults to handing the item back to
    TMDbHelper, which then offers your configured players.

    The movie form is verbatim from the probe log:
        plugin://plugin.video.themoviedb.helper/?info=play&tmdb_type=movie&tmdb_id=22
    The episode form follows the same pattern but is inferred, so it is exposed
    as a setting in case TMDbHelper expects different parameter names.
    """
    template_movie = _setting(
        "play_url_movie",
        "plugin://plugin.video.themoviedb.helper/?info=play&tmdb_type=movie&tmdb_id={tmdb_id}")
    template_episode = _setting(
        "play_url_episode",
        "plugin://plugin.video.themoviedb.helper/?info=play&tmdb_type=tv"
        "&tmdb_id={tmdb_id}&season={season}&episode={episode}")

    if not item.get("tmdb_id"):
        return ""
    try:
        if item["kind"] == "episode":
            if item.get("season") is None or item.get("episode") is None:
                return ""
            return template_episode.format(
                tmdb_id=item["tmdb_id"], season=item["season"],
                episode=item["episode"])
        return template_movie.format(tmdb_id=item["tmdb_id"])
    except (KeyError, IndexError):
        return ""


def _build_art(item):
    art = item.get("art") or {}
    resolved = {}
    for target, candidates in ART_MAP:
        for key in candidates:
            if art.get(key):
                resolved[target] = art[key]
                break
    if not resolved:
        resolved = {"poster": FALLBACK_ART, "thumb": FALLBACK_ART,
                    "icon": FALLBACK_ART}
    resolved.setdefault("icon", resolved.get("poster", FALLBACK_ART))
    return resolved


def _label(item):
    suffix = ""
    if _setting("show_percent", "false") == "true" and item.get("percent"):
        suffix = "  [{0}%]".format(int(item["percent"]))
    return _base_label(item) + suffix


def _base_label(item):
    if item["kind"] == "episode":
        show = item.get("show_title") or "Unknown"
        season = item.get("season")
        episode = item.get("episode")
        if season is not None and episode is not None:
            return "{0} - {1}x{2:02d}".format(show, season, episode)
        if item.get("absolute_number"):
            return "{0} - {1}".format(show, item["absolute_number"])
        return show
    return item.get("title") or "Unknown"


def _apply_info(li, item, position, duration):
    """
    Kodi 21 honours the InfoTagVideo setters -- the probe confirmed
    'InfoTagVideo setters available: True'. The legacy resumetime/totaltime
    properties are set alongside so the same code works on older builds.
    """
    try:
        tag = li.getVideoInfoTag()
        tag.setMediaType("episode" if item["kind"] == "episode" else "movie")
        tag.setTitle(item.get("title") or _label(item))
        if item["kind"] == "episode":
            if item.get("show_title"):
                tag.setTvShowTitle(item["show_title"])
            if item.get("season") is not None:
                tag.setSeason(int(item["season"]))
            if item.get("episode") is not None:
                tag.setEpisode(int(item["episode"]))
        if item.get("year"):
            tag.setYear(int(item["year"]))

        unique = {}
        for key, field in (("tmdb", "tmdb_id"), ("tvdb", "tvdb_id"),
                           ("imdb", "imdb_id"), ("anidb", "anidb_id")):
            if item.get(field):
                unique[key] = str(item[field])
        if unique:
            tag.setUniqueIDs(unique, "tmdb" if "tmdb" in unique else None)

        if duration:
            tag.setDuration(int(duration))
            tag.setResumePoint(float(position), float(duration))
    except Exception as exc:
        log("InfoTagVideo setters unavailable, using legacy path: {0}".format(exc),
            xbmc.LOGWARNING)

    if duration:
        li.setProperty("resumetime", str(int(position)))
        li.setProperty("totaltime", str(int(duration)))
        li.setProperty("percentplayed", str(int(100.0 * position / duration)))


def _message_directory(handle, message):
    li = xbmcgui.ListItem(label=message)
    li.setArt({"icon": FALLBACK_ART, "poster": FALLBACK_ART})
    xbmcplugin.addDirectoryItem(handle, "", li, False)
    xbmcplugin.endOfDirectory(handle)


def build_watched(handle):
    """Watched history, most recent first. No resume points -- these are done."""
    store = Store()
    if not store.configured:
        return _message_directory(handle, _(30070))

    data = store.watched(limit=int(_setting("widget_limit", "50") or 50))
    if data is None:
        return _message_directory(handle, _(30071))

    items = data.get("items", [])
    xbmcplugin.setContent(handle, "movies")
    for item in items:
        li = xbmcgui.ListItem(label=_label(item))
        li.setArt(_build_art(item))
        _apply_info(li, item, 0, 0)
        try:
            li.getVideoInfoTag().setPlaycount(1)
        except Exception:
            li.setProperty("playcount", "1")
        url = _play_url(item)
        li.setProperty("IsPlayable", "true" if url else "false")
        xbmcplugin.addDirectoryItem(handle, url, li, False)

    xbmcplugin.endOfDirectory(handle, cacheToDisc=False)
    log("listed {0} watched items".format(len(items)))


def build_recommended(handle, media_type=None, tier=None, genres=None):
    """
    Recommendations generated from your own watch history via TMDB.

    Everything is settable on the widget path, so you can have several rows:
        ?list=recommended
        ?list=recommended&media_type=tv
        ?list=recommended&tier=obscure
        ?list=recommended&media_type=tv&tier=under_the_radar&genres=10765
    """
    store = Store()
    if not store.configured:
        return _message_directory(handle, _(30070))

    media_type = "tv" if (media_type or "").lower() in ("tv", "show",
                                                        "episode") else "movie"
    data = store.recommendations(
        media_type=media_type,
        tier=tier or _setting("rec_tier", "any") or "any",
        genres=genres or "",
        limit=int(_setting("widget_limit", "50") or 50))
    if data is None:
        return _message_directory(
            handle, "Recommendations unavailable (is TMDB configured?)")

    items = data.get("items", [])
    if not items:
        return _message_directory(handle, _(30074))

    xbmcplugin.setContent(handle, "tvshows" if media_type == "tv" else "movies")
    for item in items:
        year = item.get("year")
        label = item.get("title") or "Unknown"
        li = xbmcgui.ListItem(label=label)

        art = {}
        for key, target in (("poster", "poster"), ("fanart", "fanart")):
            if (item.get("art") or {}).get(key):
                art[target] = item["art"][key]
        art.setdefault("poster", FALLBACK_ART)
        art.setdefault("thumb", art["poster"])
        art.setdefault("icon", art["poster"])
        li.setArt(art)

        try:
            tag = li.getVideoInfoTag()
            tag.setMediaType("tvshow" if media_type == "tv" else "movie")
            tag.setTitle(label)
            if year:
                tag.setYear(int(year))
            if item.get("overview"):
                tag.setPlot(item["overview"])
            if item.get("vote_average"):
                tag.setRating(float(item["vote_average"]))
            if item.get("tmdb_id"):
                tag.setUniqueIDs({"tmdb": str(item["tmdb_id"])}, "tmdb")
        except Exception as exc:
            log("info tag failed on recommendation: {0}".format(exc),
                xbmc.LOGWARNING)

        # A recommended show has no episode yet, so send it to TMDbHelper's
        # info screen rather than trying to play something unspecified.
        if media_type == "tv":
            url = _setting(
                "info_url_tv",
                "plugin://plugin.video.themoviedb.helper/?info=details"
                "&tmdb_type=tv&tmdb_id={tmdb_id}").format(
                    tmdb_id=item.get("tmdb_id"))
            li.setProperty("IsPlayable", "false")
            xbmcplugin.addDirectoryItem(handle, url, li, True)
        else:
            url = _play_url({"kind": "movie", "tmdb_id": item.get("tmdb_id")})
            li.setProperty("IsPlayable", "true" if url else "false")
            xbmcplugin.addDirectoryItem(handle, url, li, False)

    xbmcplugin.endOfDirectory(handle, cacheToDisc=False)
    log("listed {0} {1} recommendations (tier {2}, cached={3})".format(
        len(items), media_type, data.get("tier"), data.get("cached")))


def build_directory(handle):
    store = Store()
    if not store.configured:
        li = xbmcgui.ListItem(label=_(30070))
        li.setArt({"icon": FALLBACK_ART})
        xbmcplugin.addDirectoryItem(handle, "", li, False)
        xbmcplugin.endOfDirectory(handle)
        return

    data = store.in_progress(limit=int(_setting("widget_limit", "50") or 50))
    if data is None:
        li = xbmcgui.ListItem(label=_(30071))
        li.setArt({"icon": FALLBACK_ART})
        xbmcplugin.addDirectoryItem(handle, "", li, False)
        xbmcplugin.endOfDirectory(handle)
        return

    items = data.get("items", [])
    xbmcplugin.setContent(handle, "episodes" if all(
        i["kind"] == "episode" for i in items) and items else "movies")

    for item in items:
        position = float(item.get("position_sec") or 0)
        duration = float(item.get("duration_sec") or 0)

        li = xbmcgui.ListItem(label=_label(item))
        li.setArt(_build_art(item))
        _apply_info(li, item, position, duration)

        url = _play_url(item)
        li.setProperty("IsPlayable", "true" if url else "false")

        context = []
        if item.get("media_id"):
            context.append((
                _(30075),
                "RunPlugin(plugin://{0}/?action=remove&media_id={1})".format(
                    ADDON.getAddonInfo("id"), item["media_id"])))
            context.append((
                _(30076),
                "RunPlugin(plugin://{0}/?action=mark_watched&media_id={1})".format(
                    ADDON.getAddonInfo("id"), item["media_id"])))
        if context:
            li.addContextMenuItems(context)

        xbmcplugin.addDirectoryItem(handle, url, li, False)

    xbmcplugin.endOfDirectory(handle, cacheToDisc=False)
    log("listed {0} in-progress items".format(len(items)))


def remove_item(media_id):
    store = Store()
    store.call("DELETE", "/progress/{0}".format(int(media_id)))
    xbmc.executebuiltin("Container.Refresh")


def mark_watched(media_id):
    """Marks watched via the media_id we already hold, so no identity round
    trip is needed."""
    store = Store()
    row = store.call("DELETE", "/progress/{0}".format(int(media_id)))
    if row is not None:
        xbmcgui.Dialog().notification("Scrobble", _(30078),
                                      xbmcgui.NOTIFICATION_INFO, 3000)
    xbmc.executebuiltin("Container.Refresh")


def build_root(handle):
    """
    What you get when you open the addon rather than pointing a widget at it.
    Previously this went straight to the in-progress list, which meant there
    was no way to reach anything else without typing plugin paths by hand.
    """
    addon_id = ADDON.getAddonInfo("id")
    entries = [
        (30060, "?list=progress", "DefaultInProgressShows.png"),
        (30061, "?list=watched", "DefaultTVShows.png"),
        (30062, "?list=recommended&media_type=movie", "DefaultMovies.png"),
        (30063, "?list=recommended&media_type=tv", "DefaultTVShows.png"),
        (30064, "?list=recommended&tier=obscure", "DefaultAddonVideo.png"),
    ]

    store = Store()
    if not store.configured:
        li = xbmcgui.ListItem(label=_(30070))
        li.setArt({"icon": "DefaultAddonNone.png", "poster": FALLBACK_ART})
        xbmcplugin.addDirectoryItem(handle, "", li, False)
    else:
        for label_id, query, icon in entries:
            li = xbmcgui.ListItem(label=_(label_id))
            li.setArt({"icon": icon, "thumb": icon, "poster": icon})
            li.setProperty("IsPlayable", "false")
            xbmcplugin.addDirectoryItem(
                handle, "plugin://{0}/{1}".format(addon_id, query), li, True)

    # Store status and Settings, always available.
    li = xbmcgui.ListItem(label=_(30065))
    li.setArt({"icon": "DefaultIconInfo.png"})
    xbmcplugin.addDirectoryItem(
        handle, "plugin://{0}/?action=stats".format(addon_id), li, False)

    li = xbmcgui.ListItem(label=_(30066))
    li.setArt({"icon": "DefaultAddonProgram.png"})
    xbmcplugin.addDirectoryItem(
        handle, "plugin://{0}/?action=settings".format(addon_id), li, False)

    xbmcplugin.endOfDirectory(handle, cacheToDisc=False)


def route(argv):
    handle = int(argv[1])
    params = dict(urllib.parse.parse_qsl(argv[2].lstrip("?")))
    action = params.get("action")

    if action == "remove":
        remove_item(params.get("media_id"))
        return

    if action == "settings":
        ADDON.openSettings()
        return

    if action == "mark_watched":
        mark_watched(params.get("media_id"))
        return

    if action:
        from . import actions
        if actions.run(action):
            return

    which = (params.get("list") or "").lower()
    if which == "watched":
        build_watched(handle)
    elif which in ("recommended", "recommendations", "recs"):
        build_recommended(handle,
                          media_type=params.get("media_type"),
                          tier=params.get("tier"),
                          genres=params.get("genres"))
    elif which == "progress":
        build_directory(handle)
    elif not params:
        # Bare plugin:// -- the menu. A widget pointed here still gets the
        # in-progress list via ?list=progress, so nothing existing breaks.
        build_root(handle)
    else:
        build_directory(handle)

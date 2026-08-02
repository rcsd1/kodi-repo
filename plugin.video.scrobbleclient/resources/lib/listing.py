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


def build_watched(handle, kind="", show=None):
    """Watched history, most recent first. No resume points -- these are done."""
    store = Store()
    if not store.configured:
        return _message_directory(handle, _(30070))

    data = store.watched(limit=int(_setting("widget_limit", "50") or 50),
                         kind=kind, show=show)
    if data is None:
        return _message_directory(handle, _(30071))

    items = data.get("items", [])
    xbmcplugin.setContent(handle, _content_for(items, kind))
    for item in items:
        li = xbmcgui.ListItem(label=_label(item))
        li.setArt(_build_art(item))
        _apply_info(li, item, 0, 0)
        li.addContextMenuItems(_rate_context(item))
        try:
            li.getVideoInfoTag().setPlaycount(1)
        except Exception:
            li.setProperty("playcount", "1")
        url = _play_url(item)
        li.setProperty("IsPlayable", "true" if url else "false")
        xbmcplugin.addDirectoryItem(handle, url, li, False)

    xbmcplugin.endOfDirectory(handle, cacheToDisc=False)
    log("listed {0} watched items".format(len(items)))


def build_recommended(handle, media_type=None, tier=None, genres=None,
                      languages=None, exclude_genres=None, min_votes=None):
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
    # Anything given on the widget path wins over the setting, so several rows
    # can use different filters without reconfiguring the addon.
    try:
        setting_votes = int(_setting("rec_min_votes", "0") or 0)
    except ValueError:
        setting_votes = 0

    data = store.recommendations(
        media_type=media_type,
        tier=tier or _setting("rec_tier", "any") or "any",
        genres=genres or "",
        languages=languages or _setting("rec_languages", "") or "",
        exclude_genres=exclude_genres or _setting("rec_exclude_genres", "") or "",
        min_votes=int(min_votes) if min_votes else setting_votes,
        limit=int(_setting("widget_limit", "50") or 50))
    if data is None:
        err = getattr(store, "last_error", None) or {}
        if err.get("code") == 503 and "TMDB" in str(err.get("detail", "")):
            return _message_directory(handle, _(30088))
        return _message_directory(handle, _(30071))

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
            # info=seasons lands directly on the season/episode grid.
            # info=details stops on a summary page needing another click.
            url = _setting(
                "info_url_tv",
                "plugin://plugin.video.themoviedb.helper/?info=seasons"
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


def _content_for(items, kind):
    """Kodi styles rows by content type, so mixed lists get the wrong layout."""
    if kind == "movie":
        return "movies"
    if kind == "episode":
        return "episodes"
    if items and all(i["kind"] == "episode" for i in items):
        return "episodes"
    return "movies"


def _rate_context(item):
    """Rating is a context action, never a prompt. Nothing pops up after
    playback unless you go looking for it."""
    if not item.get("media_id"):
        return []
    addon_id = ADDON.getAddonInfo("id")
    entries = [(_(30084),
                "RunPlugin(plugin://{0}/?action=rate&media_id={1})".format(
                    addon_id, item["media_id"]))]
    if item.get("rating"):
        entries.append((_(30086),
                        "RunPlugin(plugin://{0}/?action=unrate&media_id={1})".format(
                            addon_id, item["media_id"])))
    return entries


def build_shows(handle, which="progress"):
    """
    A list of SHOWS, not episodes.

    This is what a TV row should be: the series poster, and how much of it is
    left. A flat list of episodes from unrelated series has nothing tying the
    entries together and no useful sort order.
    """
    store = Store()
    if not store.configured:
        return _message_directory(handle, _(30070))

    limit = int(_setting("widget_limit", "50") or 50)
    data = (store.in_progress_shows(limit) if which == "progress"
            else store.watched_shows(limit))
    if data is None:
        return _message_directory(handle, _(30071))

    items = data.get("items", [])
    if not items:
        return _message_directory(handle, _(30073))

    addon_id = ADDON.getAddonInfo("id")
    xbmcplugin.setContent(handle, "tvshows")

    for item in items:
        label = item.get("title") or "Unknown"
        li = xbmcgui.ListItem(label=label)

        art = item.get("art") or {}
        resolved = {}
        for target in ("poster", "thumb", "fanart"):
            if art.get(target):
                resolved[target] = art[target]
        if "poster" in resolved:
            resolved.setdefault("thumb", resolved["poster"])
        if not resolved:
            resolved = {"poster": FALLBACK_ART, "thumb": FALLBACK_ART}
        resolved.setdefault("icon", resolved.get("poster", FALLBACK_ART))
        li.setArt(resolved)

        # Each setter is attempted independently. Grouping them means one
        # unavailable method on an older Kodi silently skips the rest --
        # which is how the episode counts went missing.
        try:
            tag = li.getVideoInfoTag()
        except Exception as exc:
            tag = None
            log("no info tag available: {0}".format(exc), xbmc.LOGWARNING)

        if tag is not None:
            for setter, value in (
                ("setMediaType", "tvshow"),
                ("setTitle", label),
                ("setTvShowTitle", label),
                ("setYear", int(item["year"]) if item.get("year") else None),
                ("setPlot", item.get("overview")),
            ):
                if value is None:
                    continue
                try:
                    getattr(tag, setter)(value)
                except Exception:
                    pass
            if item.get("tmdb_id"):
                try:
                    tag.setUniqueIDs({"tmdb": str(item["tmdb_id"])}, "tmdb")
                except Exception:
                    pass

        # Skins draw the unwatched-count bubble from these properties, which is
        # the "19 remaining" badge on the poster. Set outside the tag block so
        # they always land.
        if item.get("total_episodes"):
            li.setProperty("TotalEpisodes", str(item["total_episodes"]))
        if item.get("watched_count") is not None:
            li.setProperty("WatchedEpisodes", str(item["watched_count"]))
        # Only the three properties Kodi defines. Inventing extras like
        # InProgressEpisodes made skins draw the in-progress clock instead of
        # the episode-count bubble.
        #
        # A count of zero is also suppressed: a show with nothing left draws a
        # watched tick, which is more informative than a "0" badge.
        remaining = item.get("remaining")
        if remaining is None and item.get("total_episodes"):
            remaining = item["total_episodes"]
        if remaining:
            li.setProperty("UnWatchedEpisodes", str(remaining))

        li.setProperty("IsPlayable", "false")
        # Straight to the full episode grid -- every season, marked up -- not
        # a filtered list of only what has been watched.
        url = "plugin://{0}/?list=show&show={1}".format(
            addon_id, item.get("tmdb_id"))
        xbmcplugin.addDirectoryItem(handle, url, li, True)

    xbmcplugin.endOfDirectory(handle, cacheToDisc=False)
    log("listed {0} shows ({1})".format(len(items), which))


def build_show_episodes(handle, tmdb_id, season=None):
    """
    Every episode of one show, with watched ticks and progress rings.

    The list comes from TMDB rather than from the watch history, so unwatched
    episodes appear too -- otherwise there is no way to see what is next.
    """
    store = Store()
    if not store.configured:
        return _message_directory(handle, _(30070))

    data = store.show_episodes(tmdb_id, season)
    if data is None:
        return _message_directory(handle, _(30071))

    items = data.get("items", [])
    if not items:
        return _message_directory(handle, _(30089))

    addon_id = ADDON.getAddonInfo("id")
    xbmcplugin.setContent(handle, "episodes")

    # Season shortcuts when the whole show is being shown at once.
    if season is None and len(data.get("seasons", [])) > 1:
        for number in data["seasons"]:
            li = xbmcgui.ListItem(label="{0} {1}".format(_(30090), number))
            li.setArt({"icon": "DefaultTVShows.png",
                       "poster": (items[0]["art"].get("poster")
                                  or FALLBACK_ART)})
            li.setProperty("IsPlayable", "false")
            xbmcplugin.addDirectoryItem(
                handle,
                "plugin://{0}/?list=show&show={1}&season={2}".format(
                    addon_id, tmdb_id, number),
                li, True)

    for item in items:
        label = "{0}x{1:02d}. {2}".format(
            item["season"], item["episode"], item.get("title") or "Episode")
        li = xbmcgui.ListItem(label=label)

        art = item.get("art") or {}
        resolved = {k: v for k, v in art.items() if v}
        if not resolved:
            resolved = {"thumb": FALLBACK_ART}
        resolved.setdefault("icon", resolved.get("thumb", FALLBACK_ART))
        li.setArt(resolved)

        duration = item.get("duration_sec") or (
            (item.get("runtime") or 0) * 60)
        position = item.get("position_sec") or 0

        try:
            tag = li.getVideoInfoTag()
            for setter, value in (
                ("setMediaType", "episode"),
                ("setTitle", item.get("title")),
                ("setTvShowTitle", item.get("show_title") or data.get("title")),
                ("setSeason", item.get("season")),
                ("setEpisode", item.get("episode")),
                ("setPlot", item.get("overview")),
                ("setFirstAired", item.get("air_date")),
                ("setPlaycount", item.get("play_count") or 0),
            ):
                if value is None:
                    continue
                try:
                    getattr(tag, setter)(value)
                except Exception:
                    pass
            if duration:
                try:
                    tag.setDuration(int(duration))
                except Exception:
                    pass
            if position > 0 and duration:
                try:
                    tag.setResumePoint(float(position), float(duration))
                except Exception:
                    pass
            if item.get("rating"):
                try:
                    tag.setUserRating(int(item["rating"]))
                except Exception:
                    pass
        except Exception as exc:
            log("episode info tag failed: {0}".format(exc), xbmc.LOGWARNING)

        if position > 0 and duration:
            li.setProperty("resumetime", str(int(position)))
            li.setProperty("totaltime", str(int(duration)))

        url = _play_url({"kind": "episode", "tmdb_id": tmdb_id,
                         "season": item["season"], "episode": item["episode"]})
        li.setProperty("IsPlayable", "true" if url else "false")

        context = []
        if item.get("media_id"):
            context.extend(_rate_context(item))
            context.append((
                _(30075),
                "RunPlugin(plugin://{0}/?action=remove&media_id={1})".format(
                    addon_id, item["media_id"])))
        if context:
            li.addContextMenuItems(context)

        xbmcplugin.addDirectoryItem(handle, url, li, False)

    xbmcplugin.endOfDirectory(handle, cacheToDisc=False)
    log("listed {0} episodes for show {1}".format(len(items), tmdb_id))


def build_directory(handle, kind="", show=None):
    store = Store()
    if not store.configured:
        li = xbmcgui.ListItem(label=_(30070))
        li.setArt({"icon": FALLBACK_ART})
        xbmcplugin.addDirectoryItem(handle, "", li, False)
        xbmcplugin.endOfDirectory(handle)
        return

    data = store.in_progress(limit=int(_setting("widget_limit", "50") or 50),
                             kind=kind, show=show)
    if data is None:
        li = xbmcgui.ListItem(label=_(30071))
        li.setArt({"icon": FALLBACK_ART})
        xbmcplugin.addDirectoryItem(handle, "", li, False)
        xbmcplugin.endOfDirectory(handle)
        return

    items = data.get("items", [])
    xbmcplugin.setContent(handle, _content_for(items, kind))

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
            context.extend(_rate_context(item))
        if context:
            li.addContextMenuItems(context)

        xbmcplugin.addDirectoryItem(handle, url, li, False)

    xbmcplugin.endOfDirectory(handle, cacheToDisc=False)
    log("listed {0} in-progress items".format(len(items)))


def remove_item(media_id):
    store = Store()
    store.call("DELETE", "/progress/{0}".format(int(media_id)))
    xbmc.executebuiltin("Container.Refresh")


def rate_item(media_id):
    """A 1-10 picker, opened on demand. No post-playback prompt exists."""
    labels = [str(n) for n in range(10, 0, -1)]
    choice = xbmcgui.Dialog().select(_(30085), labels)
    if choice < 0:
        return
    rating = int(labels[choice])
    result = Store().call("POST", "/ratings/by-media",
                          {"media_id": int(media_id), "rating": rating})
    if result is not None:
        xbmcgui.Dialog().notification("Scrobble", "{0}: {1}/10".format(
            _(30085), rating), xbmcgui.NOTIFICATION_INFO, 3000)
    xbmc.executebuiltin("Container.Refresh")


def unrate_item(media_id):
    Store().call("DELETE", "/ratings/{0}".format(int(media_id)))
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
        (30080, "?list=progress&kind=movie", "DefaultMovies.png"),
        (30081, "?list=progress&kind=episode", "DefaultTVShows.png"),
        (30061, "?list=watched", "DefaultTVShows.png"),
        (30082, "?list=watched&kind=movie", "DefaultMovies.png"),
        (30083, "?list=watched&kind=episode", "DefaultTVShows.png"),
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

    if action == "rate":
        rate_item(params.get("media_id"))
        return

    if action == "unrate":
        unrate_item(params.get("media_id"))
        return

    if action:
        from . import actions
        if actions.run(action):
            return

    which = (params.get("list") or "").lower()
    kind = (params.get("kind") or "").lower()
    if kind not in ("movie", "episode"):
        kind = ""
    try:
        show = int(params.get("show") or 0) or None
    except ValueError:
        show = None

    # Asking for TV without naming a show means the list of shows.
    if kind == "episode" and not show and which in ("progress", "watched"):
        build_shows(handle, which)
        return

    # ?list=show&show=<tmdb id> is the full season/episode grid.
    if which == "show" and show:
        try:
            season = int(params["season"]) if "season" in params else None
        except (ValueError, TypeError):
            season = None
        build_show_episodes(handle, show, season)
        return

    if which == "watched":
        build_watched(handle, kind=kind, show=show)
    elif which in ("recommended", "recommendations", "recs"):
        build_recommended(handle,
                          media_type=params.get("media_type"),
                          tier=params.get("tier"),
                          genres=params.get("genres"),
                          languages=params.get("languages"),
                          exclude_genres=params.get("exclude_genres"),
                          min_votes=params.get("min_votes"))
    elif which == "progress":
        build_directory(handle, kind=kind, show=show)
    elif not params:
        # Bare plugin:// -- the menu. A widget pointed here still gets the
        # in-progress list via ?list=progress, so nothing existing breaks.
        build_root(handle)
    else:
        build_directory(handle, kind=kind, show=show)

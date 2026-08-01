"""
Pull a store identity out of whatever is playing.

Behaviour here is dictated by probe results rather than guesswork:

  * JSON-RPC Player.GetItem is useless for plugin-launched playback. It reported
    type "unknown", an empty title, and either no uniqueid key at all or one
    keyed "unknown". Not used.

  * InfoTagVideo and infolabels both work and populate at t=0-2ms. No polling
    race exists -- v0.1 of the probe suggested one and v0.2 disproved it.

  * Seren never populates getUniqueID(). Its only identifier is
    getIMDBNumber(), and that is episode-level.

  * Umbrella populates tmdb/tvdb/imdb, all show-level.

  * Otaku populates anidb and reports absolute episode numbers under season 1.
"""

import json

import xbmc

ART_KEYS = ("poster", "thumb", "fanart", "landscape", "clearlogo", "banner")


def _infolabel(name):
    try:
        val = xbmc.getInfoLabel(name)
    except Exception:
        return None
    return val.strip() if val else None


def _int_or_none(value):
    try:
        n = int(value)
    except (TypeError, ValueError):
        return None
    return None if n < 0 else n


def _tag_unique(tag, key):
    try:
        val = tag.getUniqueID(key)
    except Exception:
        return None
    return val.strip() if val else None


def _collect_art(tag):
    """
    Artwork so the Resume Watching widget has something to draw. The probe
    showed art arriving as image:// wrapped TMDB urls; unwrap where possible so
    the values stay portable across devices.
    """
    art = {}
    for key in ART_KEYS:
        value = _infolabel("Player.Art({0})".format(key)) or \
            _infolabel("VideoPlayer.Art({0})".format(key))
        if value:
            art[key] = value
    # Episodes: fall back to show artwork, which is what a widget wants anyway.
    for key in ("tvshow.poster", "tvshow.fanart", "tvshow.clearlogo"):
        value = _infolabel("VideoPlayer.Art({0})".format(key))
        if value:
            art[key.replace("tvshow.", "show_")] = value
    return art


def extract(player=None):
    """Returns an identity dict for the store, or None if nothing usable."""
    player = player or xbmc.Player()
    try:
        tag = player.getVideoInfoTag()
    except Exception:
        return None
    if tag is None:
        return None

    try:
        media_type = (tag.getMediaType() or "").lower()
    except Exception:
        media_type = ""

    kind = "episode" if media_type == "episode" else "movie"

    tmdb = _tag_unique(tag, "tmdb") or _infolabel("VideoPlayer.UniqueID(tmdb)")
    tvdb = _tag_unique(tag, "tvdb") or _infolabel("VideoPlayer.UniqueID(tvdb)")
    imdb = _tag_unique(tag, "imdb") or _infolabel("VideoPlayer.UniqueID(imdb)")
    anidb = _tag_unique(tag, "anidb") or _infolabel("VideoPlayer.UniqueID(anidb)")

    # Seren's only identifier. getUniqueID('imdb') is empty for it, but
    # getIMDBNumber() is populated -- on every single probe playback.
    if not imdb:
        try:
            imdb = tag.getIMDBNumber() or None
        except Exception:
            imdb = None
    if not imdb:
        imdb = _infolabel("VideoPlayer.IMDBNumber")

    if imdb and not imdb.startswith("tt"):
        # Some scrapers put a numeric tmdb id in the IMDB field.
        if not tmdb:
            tmdb = imdb
        imdb = None

    try:
        season = _int_or_none(tag.getSeason())
        episode = _int_or_none(tag.getEpisode())
        title = tag.getTitle() or None
        show_title = tag.getTVShowTitle() or None
        year = _int_or_none(tag.getYear())
    except Exception:
        season = _int_or_none(_infolabel("VideoPlayer.Season"))
        episode = _int_or_none(_infolabel("VideoPlayer.Episode"))
        title = _infolabel("VideoPlayer.Title")
        show_title = _infolabel("VideoPlayer.TVShowTitle")
        year = _int_or_none(_infolabel("VideoPlayer.Year"))

    if kind == "movie":
        season = episode = None

    identity = {
        "kind": kind,
        "tmdb_id": _int_or_none(tmdb),
        "tvdb_id": _int_or_none(tvdb),
        "imdb_id": imdb,
        "anidb_id": _int_or_none(anidb),
        "season": season,
        "episode": episode,
        "title": title,
        "show_title": show_title,
        "year": year,
        "art": _collect_art(tag),
    }

    # Nothing to key on at all -- refuse rather than write junk.
    if not any((identity["tmdb_id"], identity["tvdb_id"], identity["imdb_id"],
                identity["anidb_id"], identity["show_title"], identity["title"])):
        return None

    return identity


def describe(identity):
    if not identity:
        return "<unidentified>"
    if identity["kind"] == "episode":
        return "{0} S{1}E{2}".format(
            identity.get("show_title") or "?",
            identity.get("season") if identity.get("season") is not None else "?",
            identity.get("episode") if identity.get("episode") is not None else "?")
    return "{0} ({1})".format(identity.get("title") or "?",
                              identity.get("year") or "?")


def to_json(identity):
    return json.dumps(identity, sort_keys=True)

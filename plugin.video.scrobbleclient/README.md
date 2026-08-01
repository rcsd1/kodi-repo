# Scrobble Client (Kodi)

Two components in one addon:

- **Service** — scrobbles playback to your store, resumes where you left off,
  and detects when Kodi hands the stream to an external player
- **Plugin** — serves the Resume Watching list with progress rings

Works with Seren, Umbrella, Fen and Otaku. Runs on macOS, Linux, Windows and
Android (Fire Stick) from the same zip.

---

## Install

Kodi → Add-ons → Install from zip file → `plugin.video.scrobbleclient.zip`

Then Configure:

| Setting | Value |
|---|---|
| Store URL | `https://your-store.example.com` (no trailing slash) |
| Device token | the token minted for **this** device |

Use a different token per device. That is what lets you revoke the Fire Stick
without re-authorising the Mac, and it is how progress writes get attributed.

Check it took: Kodi log should show `store reachable: True`.

---

## Point your widget at it

```
plugin://plugin.video.scrobbleclient/
```

Skin Settings → Home → Customise home menu → highlight a menu item → Widget.

Set **Content type** to `movies` or `episodes` rather than `videos` — Arctic
Horizon styles the row differently when it knows what it is looking at.

The probe already confirmed progress rings render correctly in an AH2 widget on
your setup, so Kodi issue #25045 is not a concern here.

---

## How it decides whether to scrobble

Not a setting. Detected, every playback:

```
getTotalTime() > 0   ->  Kodi is decoding  ->  scrobble and resume
getTotalTime() == 0  ->  external player   ->  write handoff, stay out of it
```

Probe evidence: native playback reported 5087.926 and 1415.807 immediately,
while all five mpv-delegated playbacks reported 0.0. Two clean populations.

So the same addon does the right thing on the Fire Stick (native, scrobbles)
and on the Mac with playercorefactory enabled (delegates, hands off). Disable
playercorefactory and the Mac starts scrobbling on its own with no
reconfiguration.

### The handoff file

When Kodi delegates, it writes `handoff.json` into the addon's profile
directory:

```json
{
  "identity": { "anidb_id": 69, "episode": 1151, "show_title": "ONE PIECE", ... },
  "stream_url": "https://...debrid.../One Piece - 1151.mkv",
  "written_at": 1785000000.0
}
```

The mpv script reads this and matches `stream_url` against its own argv. Debrid
URLs are unique per request, so the correlation is exact.

This is why anime will work where it never did before — mpv receives
`anidb=69, episode=1151` instead of a filename to guess at.

---

## Player quirks it handles

Captured from your logs, not assumed:

| Player | Identifiers | Handling |
|---|---|---|
| **Umbrella** | tmdb + tvdb + imdb, show-level | used directly |
| **Seren** | `getIMDBNumber()` only, **episode-level** | falls back to IMDB number, relies on show title + coordinates to converge |
| **Otaku** | anidb + **absolute** episode under season 1 | anidb captured, absolute number flagged for mapping |
| **Fen** | untested — was crashing on dead Trakt auth | should behave like the others once it plays |

Also handled: `-1` sentinels from `getSeason()`/`getEpisode()` on movies,
negative `getTime()` at playback start, and `getDuration()` returning 0 while
`getTotalTime()` is correct.

---

## Behaviour

- Progress saved every 60 seconds during playback, and on stop
- Reaching the end marks watched
- Under 60 seconds (configurable) is discarded rather than saved
- Failed writes queue to disk and flush every 5 minutes
- Kodi closing mid-playback still saves the position

## Context menu

Right-click any item in Resume Watching → **Remove from Resume Watching**.
The thing Trakt never let you do without a third-party tool.

---

## Tests

```
python -m pytest test_client.py -q
```

Sixteen tests with the Kodi runtime stubbed. Every player fixture reproduces
values captured from your actual probe runs.

---

## Android notes

- All paths go through `xbmcvfs.translatePath` — raw paths work on macOS and
  fail on Android
- No third-party Python modules; `urllib` from Kodi's bundled Python only
- Service is event-driven with a 5-second idle tick, so it stays out of the way
  on a 4K Max that is already working hard

# Scrobble

Scrobble thing to replace Trakt. Self-hosted, so no connection limits and
resume points that never expire.

## What it does

- Watched history for movies and individual episodes
- Resume points, kept indefinitely
- Works across devices — mpv on a laptop, Kodi on a TV
- Anime with absolute episode numbers (One Piece 1151, not S01E1151)
- Recommendations for movies and TV, filterable by popularity tier
- Works with debrid

## Install

Add as a file source in Kodi:

```
https://rcsd1.github.io/kodi-repo/zips/
```

Install from zip file → `repository.scrobble`

Install from repository → Scrobble Repository → Video add-ons → Scrobble Client

Needs a server to point at. That part isn't public.

## Update

```
cd ~/scrobble/kodi-repo
./update-addon.sh ~/Downloads/scrobble-complete
```

Then Check for updates in Kodi.

## Licence

MIT

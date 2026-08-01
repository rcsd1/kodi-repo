# Scrobble

I got tired of Trakt.

In July 2026 they limited free accounts to a single connected app. I was
running five — Seren, Fen, Umbrella, Otaku and TMDbHelper — plus couchmoney for
recommendations. One morning my scrobbler's token refresh came back 400, wiped
itself, and couldn't reconnect because Kodi was occupying the only slot I had
left. My "Resume Watching" row started throwing errors.

That was annoying but survivable. What actually pushed me over was realising
nobody keeps resume points for long. Trakt drops them after six months, Simkl
after seven days unless you pay, and even then ninety. If I watch half a film
and come back in March, no tracker on the market still knows where I was.

So this is mine. It runs on a free Oracle instance and holds my watch history
and resume points for as long as I want, which is forever.

## What it does

- Watched history for films and individual episodes
- Resume points that don't expire, keyed to the title rather than the file
- Cross-device: start something in mpv on my laptop, finish it on the TV
- No connection limit, because there's nobody to impose one
- Anime with absolute episode numbers — One Piece 1151, not a made-up S01E1151
- Recommendations for film and TV, filterable by how obscure you want them
- Works with debrid, where the stream URL is different every single time

## What's in here

This repository is the Kodi side: a repo addon so my devices can install and
update the client without me sideloading a zip every time.

```
repository.scrobble/            the connector Kodi installs first
plugin.video.scrobbleclient/    the actual addon
zips/                           generated, don't edit
_generate.py                    builds the zips and manifests
update-addon.sh                 ships a new version
```

The server component lives elsewhere and isn't public.

## Installing it

In Kodi, add this as a file source:

```
https://rcsd1.github.io/kodi-repo/zips/
```

Then Install from zip file → repository.scrobble, and after that Install from
repository → Scrobble Repository → Scrobble Client.

You'll need somewhere to point it at, which means running the server half. If
you've somehow ended up here without that, this addon won't do anything useful.

## Shipping an update

```
cd ~/scrobble/kodi-repo
./update-addon.sh ~/Downloads/scrobble-complete
```

It bumps the zips, regenerates the manifests, checks I haven't left a token
lying around in a file, and pushes. Then Check for updates on each device.

## A note on the ring

The icon is a progress ring because that's the thing I actually look at. It's
what tells me where I am in something, and getting it to render correctly in a
skin widget was the first real test of whether any of this was going to work.

## Licence

MIT.

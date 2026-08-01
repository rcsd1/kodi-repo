# Scrobble Kodi Repository

Hosts the Scrobble Client addon so the Fire Stick can install and **update** it
without you sideloading a zip every time.

## One-time setup

1. Create a **public** GitHub repository (name it anything, e.g. `kodi-repo`).
2. Point this at it:

   ```
   ./configure.sh yourusername kodi-repo main
   ```

3. Build it:

   ```
   python3 _generate.py
   ```

4. Push:

   ```
   git init && git add -A
   git commit -m "initial repo"
   git branch -M main
   git remote add origin https://github.com/yourusername/kodi-repo.git
   git push -u origin main
   ```

Your install URL is then:

```
https://raw.githubusercontent.com/yourusername/kodi-repo/main/zips/repository.scrobble/repository.scrobble-1.0.0.zip
```

That is the URL you feed to Downloader on the Fire Stick.

## Whenever an addon changes

1. Bump `<version>` in that addon's `addon.xml` — Kodi will not offer an update
   otherwise
2. `python3 _generate.py`
3. Commit and push

Kodi checks for updates on its own schedule; force it with
Add-ons → the update icon → Check for updates.

## Layout

```
repository.scrobble/       the repo connector addon
plugin.video.scrobbleclient/   the addon itself
zips/                      generated — do not edit by hand
_generate.py               builds zips + addons.xml + md5
configure.sh               writes your GitHub URLs into the repo addon
```

The repo must be **public**. Kodi fetches over plain HTTPS with no
authentication.

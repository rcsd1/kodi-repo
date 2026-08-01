#!/bin/sh
#
# update-addon.sh -- push a new addon version to your Kodi repository.
#
#   ./update-addon.sh ~/Downloads/scrobble-complete
#
# Run this from inside your PERMANENT kodi-repo folder -- the one that is a git
# repository and has a remote pointing at GitHub. Never re-clone or re-init it;
# this script copies the new addon into it and pushes.
#
set -e

BUNDLE="$1"
REPO_DIR="$(cd "$(dirname "$0")" && pwd)"

if [ -z "$BUNDLE" ]; then
    echo "usage: $0 /path/to/scrobble-complete"
    echo
    echo "Example:"
    echo "  cd ~/scrobble/kodi-repo"
    echo "  ./update-addon.sh ~/Downloads/scrobble-complete"
    exit 1
fi

BUNDLE="$(cd "$BUNDLE" 2>/dev/null && pwd)" || {
    echo "ERROR: no such folder: $1"; exit 1; }

SRC="$BUNDLE/plugin.video.scrobbleclient"
[ -d "$SRC" ] || { echo "ERROR: $SRC not found. Is that the bundle folder?"; exit 1; }

cd "$REPO_DIR"

if [ ! -d .git ]; then
    echo "ERROR: $REPO_DIR is not a git repository."
    echo
    echo "You are probably in a freshly unzipped bundle rather than your"
    echo "permanent repo. Find it with:"
    echo "    ls -d ~/*/kodi-repo/.git ~/Downloads/*/kodi-repo/.git 2>/dev/null"
    exit 1
fi

# Parse the addon tag rather than grepping -- the XML declaration on line one
# also contains version="1.0" and a naive grep picks that up instead.
read_version() {
    python3 -c "
import sys, xml.etree.ElementTree as ET
try:
    print(ET.parse(sys.argv[1]).getroot().get('version') or '')
except Exception:
    print('')
" "$1" 2>/dev/null
}

OLD_VERSION="$(read_version plugin.video.scrobbleclient/addon.xml)"
NEW_VERSION="$(read_version "$SRC/addon.xml")"

if [ -z "$NEW_VERSION" ]; then
    echo "ERROR: could not read a version from $SRC/addon.xml"
    exit 1
fi

echo "Repository: $REPO_DIR"
echo "Bundle:     $BUNDLE"
echo "Version:    ${OLD_VERSION:-none} -> $NEW_VERSION"
echo

if [ "$OLD_VERSION" = "$NEW_VERSION" ]; then
    echo "WARNING: version unchanged. Kodi will not offer an update."
    printf "Continue anyway? [y/N] "
    read answer
    case "$answer" in [Yy]*) ;; *) echo "Aborted."; exit 1 ;; esac
fi

# Replace the addon wholesale so deleted files actually disappear.
rm -rf plugin.video.scrobbleclient
cp -R "$SRC" plugin.video.scrobbleclient
rm -f plugin.video.scrobbleclient/test_client.py
find plugin.video.scrobbleclient -name '__pycache__' -type d -exec rm -rf {} + 2>/dev/null || true
find plugin.video.scrobbleclient -name '*.pyc' -delete 2>/dev/null || true

# Keep the generator itself current -- it occasionally changes too.
[ -f "$BUNDLE/kodi-repo/_generate.py" ] && cp "$BUNDLE/kodi-repo/_generate.py" .

echo "Rebuilding repository..."
python3 _generate.py

# --- secret scan ---------------------------------------------------------
# This repository is public. A token committed here is exposed within seconds
# and stays in git history even after the file is deleted.
echo
echo "Scanning for secrets..."
LEAKS="$(python3 - <<'PYSCAN'
import os, re, subprocess, sys

BAD_NAME = re.compile(r"\.(key|pem|conf|env)$|tokens?\.txt$|secrets", re.I)
BAD_TEXT = re.compile(
    r"BEGIN [A-Z ]*PRIVATE KEY"
    r"|ghp_[A-Za-z0-9]{20,}"
    r"|STORE_ADMIN_TOKEN\s*=\s*\S+"
    r"|Bearer\s+[A-Za-z0-9+/=_-]{25,}")
SKIP_EXT = (".zip", ".png", ".jpg", ".jpeg", ".gif", ".md5", ".pyc")

try:
    files = subprocess.check_output(
        ["git", "ls-files", "--cached", "--others", "--exclude-standard"],
        text=True).split("\n")
except Exception:
    files = []

found = []
for name in files:
    if not name:
        continue
    if BAD_NAME.search(name):
        found.append(name + " (filename)")
        continue
    if name.lower().endswith(SKIP_EXT) or not os.path.isfile(name):
        continue
    if os.path.getsize(name) > 2_000_000:
        continue
    try:
        with open(name, "r", encoding="utf-8", errors="ignore") as fh:
            if BAD_TEXT.search(fh.read()):
                found.append(name + " (content)")
    except Exception:
        pass

print("\n".join(found))
PYSCAN
)"

if [ -n "$LEAKS" ]; then
    echo
    echo "REFUSING TO PUSH -- possible secrets found:"
    echo "$LEAKS" | sed 's/^/    /'
    echo
    echo "This repository is PUBLIC. Remove these files, then run again."
    exit 1
fi
echo "   clean"

echo
echo "Committing..."
git add -A
if git diff --cached --quiet; then
    echo "Nothing changed. Done."
    exit 0
fi
git commit -m "addon $NEW_VERSION"

echo "Pushing..."
git push

echo
echo "Done. Addon $NEW_VERSION is live."
echo
echo "On each Kodi device:"
echo "  Settings > Add-ons > Check for updates"
echo "  then My add-ons > Video add-ons > Scrobble Client > Update"

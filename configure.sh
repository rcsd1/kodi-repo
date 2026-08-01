#!/bin/sh
# Point the repository at your GitHub account.
#   ./configure.sh yourusername your-repo-name [branch]
set -e
[ $# -lt 2 ] && { echo "usage: $0 <github-user> <repo-name> [branch]"; exit 1; }
USER="$1"; REPO="$2"; BRANCH="${3:-main}"
F="$(dirname "$0")/repository.scrobble/addon.xml"

# Two hosting choices:
#   raw.githubusercontent.com  - works for Kodi's repo updater, but File
#                                Manager cannot browse it (no directory index)
#   USER.github.io             - serves the generated index.html files, so
#                                File Manager can browse it too
# Pass "pages" as the 4th argument to use GitHub Pages URLs.
MODE="${4:-raw}"

if [ "$MODE" = "pages" ]; then
  BASE="https://$USER.github.io/$REPO/$BRANCH-pages"
  BASE="https://$USER.github.io/$REPO"
  sed -i.bak \
    -e "s|https://raw.githubusercontent.com/GITHUB_USER/GITHUB_REPO/main|$BASE|g" \
    -e "s|GITHUB_USER|$USER|g" \
    -e "s|GITHUB_REPO|$REPO|g" "$F"
else
  sed -i.bak \
    -e "s|GITHUB_USER|$USER|g" \
    -e "s|GITHUB_REPO|$REPO|g" \
    -e "s|/main/zips/|/$BRANCH/zips/|g" "$F"
fi
rm -f "$F.bak"

echo "Repository configured for:"
grep -o 'https://raw.githubusercontent.com[^<]*' "$F" | sed 's/^/  /'
echo
echo "Now run: python3 _generate.py"

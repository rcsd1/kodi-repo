#!/usr/bin/env python3
"""
Kodi repository generator.

Run this after changing any addon. It bumps nothing automatically -- edit the
version in the addon's addon.xml first, then run this, then commit and push.

Produces:

    zips/
        addons.xml
        addons.xml.md5
        plugin.video.scrobbleclient/plugin.video.scrobbleclient-1.0.0.zip
        repository.scrobble/repository.scrobble-1.0.0.zip

Kodi reads addons.xml to learn what versions exist, verifies it against the
md5, then fetches the zip it needs from datadir.
"""

import hashlib
import os
import pathlib
import shutil
import sys
import xml.etree.ElementTree as ET
import zipfile

HERE = os.path.dirname(os.path.abspath(__file__))
ZIPS = os.path.join(HERE, "zips")

EXCLUDE_DIRS = {".git", "__pycache__", ".pytest_cache", ".github", "zips"}
EXCLUDE_FILES = {".DS_Store", ".gitignore"}
EXCLUDE_SUFFIX = (".pyc", ".pyo", ".log", ".zip")


def addon_dirs():
    for name in sorted(os.listdir(HERE)):
        path = os.path.join(HERE, name)
        if not os.path.isdir(path) or name in EXCLUDE_DIRS:
            continue
        if os.path.isfile(os.path.join(path, "addon.xml")):
            yield name, path


def read_addon_xml(path):
    tree = ET.parse(os.path.join(path, "addon.xml"))
    root = tree.getroot()
    return root, root.get("id"), root.get("version")


def should_include(rel_path, filename):
    if filename in EXCLUDE_FILES or filename.endswith(EXCLUDE_SUFFIX):
        return False
    parts = rel_path.split(os.sep)
    return not any(part in EXCLUDE_DIRS for part in parts)


def build_zip(addon_id, version, source):
    out_dir = os.path.join(ZIPS, addon_id)
    os.makedirs(out_dir, exist_ok=True)
    out_path = os.path.join(out_dir, "{0}-{1}.zip".format(addon_id, version))

    with zipfile.ZipFile(out_path, "w", zipfile.ZIP_DEFLATED) as zf:
        for root, dirs, files in os.walk(source):
            dirs[:] = [d for d in dirs if d not in EXCLUDE_DIRS]
            for filename in files:
                full = os.path.join(root, filename)
                rel = os.path.relpath(full, source)
                if not should_include(rel, filename):
                    continue
                # Kodi requires the addon id as the top-level directory.
                zf.write(full, os.path.join(addon_id, rel))
    return out_path


def copy_assets(addon_id, source):
    """Kodi shows icon/fanart from the repo before the addon is installed."""
    for asset in ("icon.png", "fanart.jpg", "changelog.txt"):
        src = os.path.join(source, asset)
        if os.path.isfile(src):
            shutil.copy(src, os.path.join(ZIPS, addon_id, asset))


INDEX_TEMPLATE = """<!DOCTYPE html>
<html><head><meta charset="utf-8"><title>{title}</title></head>
<body>
<h1>{title}</h1>
<ul>
{links}
</ul>
</body></html>
"""


def write_indexes(addon_ids):
    """
    Kodi's File Manager can browse an HTTP source only if the server returns an
    HTML page of links -- it has no way to list a directory otherwise. GitHub's
    raw host serves individual files and nothing else, which is why adding a
    raw URL as a source reports "Couldn't retrieve directory info".

    GitHub Pages will serve these index files, so the same repo becomes
    browsable at https://USER.github.io/REPO/zips/ once Pages is enabled.
    """
    # Top level: one link per addon directory, plus the manifest files.
    links = ['<li><a href="{0}/">{0}/</a></li>'.format(a) for a in sorted(addon_ids)]
    links.append('<li><a href="addons.xml">addons.xml</a></li>')
    links.append('<li><a href="addons.xml.md5">addons.xml.md5</a></li>')
    (pathlib.Path(ZIPS) / "index.html").write_text(
        INDEX_TEMPLATE.format(title="Scrobble Repository", links="\n".join(links)))

    # Per-addon: one link per file in that directory.
    for addon_id in addon_ids:
        directory = pathlib.Path(ZIPS) / addon_id
        entries = sorted(f.name for f in directory.iterdir() if f.is_file()
                         and f.name != "index.html")
        rows = ['<li><a href="{0}">{0}</a></li>'.format(name) for name in entries]
        (directory / "index.html").write_text(
            INDEX_TEMPLATE.format(title=addon_id, links="\n".join(rows)))


def main():
    if os.path.isdir(ZIPS):
        shutil.rmtree(ZIPS)
    os.makedirs(ZIPS)

    entries = []
    found = list(addon_dirs())
    if not found:
        sys.exit("no addon folders found next to this script")

    for name, path in found:
        root, addon_id, version = read_addon_xml(path)
        if not addon_id or not version:
            print("  SKIP  {0}: addon.xml missing id or version".format(name))
            continue
        zip_path = build_zip(addon_id, version, path)
        copy_assets(addon_id, path)
        entries.append(ET.tostring(root, encoding="unicode").strip())
        print("  {0:<40} {1:<10} {2}".format(
            addon_id, version, os.path.relpath(zip_path, HERE)))

    addons_xml = '<?xml version="1.0" encoding="UTF-8" standalone="yes"?>\n<addons>\n'
    addons_xml += "\n".join(entries)
    addons_xml += "\n</addons>\n"

    xml_path = os.path.join(ZIPS, "addons.xml")
    with open(xml_path, "w", encoding="utf-8") as fh:
        fh.write(addons_xml)

    digest = hashlib.md5(addons_xml.encode("utf-8")).hexdigest()
    with open(xml_path + ".md5", "w", encoding="utf-8") as fh:
        fh.write(digest)

    write_indexes([a.split(">")[0] for a in []] or
                  [d.name for d in pathlib.Path(ZIPS).iterdir() if d.is_dir()])

    print("\naddons.xml written, md5 {0}".format(digest))
    print("index.html written for File Manager browsing")
    print("Now: git add -A && git commit -m 'update' && git push")


if __name__ == "__main__":
    main()

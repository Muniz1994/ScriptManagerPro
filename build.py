#!/usr/bin/env python3
"""Build the installable add-on zip.

Usage:
    python build.py

Writes dist/script_manager_pro-<version>.zip, laid out so that Blender accepts
it from Preferences > Add-ons (or Get Extensions) > Install from Disk.
"""

import re
import shutil
import zipfile
from pathlib import Path

ROOT = Path(__file__).parent
PKG = ROOT / "script_manager_pro"
DIST = ROOT / "dist"

# Everything else in the package folder is shipped.
EXCLUDE_DIRS = {"__pycache__", ".git"}
EXCLUDE_SUFFIXES = {".pyc", ".pyo", ".zip"}


def get_version():
    manifest = (PKG / "blender_manifest.toml").read_text(encoding="utf-8")
    match = re.search(r'^version\s*=\s*"([^"]+)"', manifest, re.MULTILINE)
    if match is None:
        raise SystemExit("no version field in blender_manifest.toml")
    return match.group(1)


def iter_files():
    for path in sorted(PKG.rglob("*")):
        if not path.is_file():
            continue
        if EXCLUDE_DIRS.intersection(path.relative_to(PKG).parts):
            continue
        if path.suffix in EXCLUDE_SUFFIXES:
            continue
        yield path


def main():
    if DIST.exists():
        shutil.rmtree(DIST)
    DIST.mkdir()

    zip_path = DIST / f"{PKG.name}-{get_version()}.zip"
    with zipfile.ZipFile(zip_path, "w", zipfile.ZIP_DEFLATED) as zf:
        for path in iter_files():
            # Keep the package folder as the zip's single top-level directory.
            zf.write(path, Path(PKG.name) / path.relative_to(PKG))

    print(f"built {zip_path.relative_to(ROOT)}")


if __name__ == "__main__":
    main()

"""Package only the built program and public docs, never home/cache/credentials."""

import argparse
import hashlib
import shutil
from pathlib import Path

parser = argparse.ArgumentParser()
parser.add_argument("platform")
args = parser.parse_args()
root = Path(__file__).resolve().parents[1]
source = root / "dist/tspotify"
if not source.is_dir():
    raise SystemExit("Build dist/tspotify with PyInstaller first")
for name in ("LICENSE", "NOTICE", "README.md", "CHANGELOG.md"):
    shutil.copy2(root / name, source / name)
shutil.copytree(root / "docs/licenses", source / "licenses", dirs_exist_ok=True)
if args.platform.startswith("macos"):
    launch = source / "Start tspotify.command"
    launch.write_text('#!/bin/sh\ncd "$(dirname "$0")"\nexec ./tspotify "$@"\n', encoding="utf-8")
    launch.chmod(0o755)
out = root / "release"
out.mkdir(exist_ok=True)
name = "tspotify-" + args.platform
archive = Path(shutil.make_archive(str(out / name), "zip", root_dir=root / "dist", base_dir="tspotify"))
digest = hashlib.sha256(archive.read_bytes()).hexdigest()
(out / (name + ".sha256")).write_text(digest + "  " + archive.name + "\n", encoding="ascii")
print(archive)

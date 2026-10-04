#!/usr/bin/env python3
import hashlib
import sys
import tarfile

from sync_upstream import verify_lock, ROOT

lock = verify_lock()
sys.path.insert(0, str(ROOT))
from natter_gui import __version__
version = __version__
name = f"natter-gui-{version}-core-{lock['tag']}"
output = ROOT / "dist"
output.mkdir(exist_ok=True)
archive = output / (name + ".tar.gz")
paths = [ROOT / x for x in ("README.md", "LICENSE", "NOTICE", "upstream.lock.json", "Dockerfile", "compose.yaml")]
for folder in ("natter_gui", "vendor", "scripts", "docs"):
    paths.extend(p for p in (ROOT / folder).rglob("*") if p.is_file() and "__pycache__" not in p.parts and p.suffix != ".pyc")
with tarfile.open(archive, "w:gz") as tar:
    for p in sorted(paths):
        tar.add(p, arcname=name + "/" + p.relative_to(ROOT).as_posix())
digest = hashlib.sha256(archive.read_bytes()).hexdigest()
(output / "SHA256SUMS").write_text(digest + "  " + archive.name + "\n")
print(archive)

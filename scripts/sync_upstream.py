#!/usr/bin/env python3
"""Fetch an official Release by immutable commit; verify before replacing files."""
import argparse
import hashlib
import json
import os
from pathlib import Path
import re
import subprocess
import sys
import tempfile
import urllib.request

ROOT = Path(__file__).resolve().parent.parent
REPO = "MikeWang000000/Natter"
FILES = {"natter.py": "natter.py", "natter-check.py": "natter-check/natter-check.py", "LICENSE": "LICENSE"}


def fetch(url):
    headers = {"User-Agent": "Natter-GUI-upstream-sync"}
    if url.startswith("https://api.github.com/") and os.environ.get("GITHUB_TOKEN"):
        headers["Authorization"] = "Bearer " + os.environ["GITHUB_TOKEN"]
    with urllib.request.urlopen(urllib.request.Request(url, headers=headers), timeout=30) as response:
        return response.read()


def verify_lock():
    lock = json.loads((ROOT / "upstream.lock.json").read_text())
    for name, digest in lock["sha256"].items():
        if hashlib.sha256((ROOT / "vendor" / "natter" / name).read_bytes()).hexdigest() != digest:
            raise ValueError("vendored file differs from pinned source: " + name)
    return lock


def main():
    parser = argparse.ArgumentParser()
    parser.add_argument("--verify", action="store_true")
    parser.add_argument("--check", action="store_true")
    args = parser.parse_args()
    current = verify_lock()
    if args.verify:
        print("Pinned upstream files verified: " + current["tag"])
        return
    release = json.loads(fetch("https://api.github.com/repos/" + REPO + "/releases/latest"))
    tag = release["tag_name"]
    if not re.fullmatch(r"[A-Za-z0-9._-]+", tag):
        raise ValueError("Unexpected release tag")
    commit = json.loads(fetch("https://api.github.com/repos/" + REPO + "/commits/" + tag))["sha"]
    if not re.fullmatch(r"[a-f0-9]{40}", commit):
        raise ValueError("Invalid upstream commit")
    if commit == current["commit"]:
        print("Already synchronized: " + tag)
        return
    print("Upstream release available: " + tag + " (" + commit + ")")
    if args.check:
        return
    contents = {name: fetch("https://raw.githubusercontent.com/" + REPO + "/" + commit + "/" + path) for name, path in FILES.items()}
    with tempfile.TemporaryDirectory() as temporary:
        directory = Path(temporary)
        for name, content in contents.items():
            (directory / name).write_bytes(content)
        for name in ("natter.py", "natter-check.py"):
            compile(contents[name], name, "exec")
        help_text = subprocess.check_output([sys.executable, str(directory / "natter.py"), "--help"], text=True, timeout=10)
        for flag in ("-m", "-i", "-b", "-t", "-p", "-e", "-U", "-u", "-r", "-k"):
            if flag not in help_text:
                raise ValueError("Upstream removed a required CLI argument: " + flag)
        version = subprocess.check_output([sys.executable, str(directory / "natter.py"), "--version"], text=True, timeout=10)
        if tag.lstrip("v") not in version:
            raise ValueError("Core version and Release tag disagree")
        if b"GNU GENERAL PUBLIC LICENSE" not in contents["LICENSE"]:
            raise ValueError("Upstream license changed; review required")
    for name, content in contents.items():
        (ROOT / "vendor" / "natter" / name).write_bytes(content)
    value = {"repository": REPO, "tag": tag, "commit": commit,
             "sha256": {name: hashlib.sha256(content).hexdigest() for name, content in contents.items()}}
    (ROOT / "upstream.lock.json").write_text(json.dumps(value, indent=2) + "\n")
    print("Updated pinned core; run full tests before committing.")


if __name__ == "__main__":
    main()

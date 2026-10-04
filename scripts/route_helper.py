#!/usr/bin/env python3
"""Root-only, fixed-scope UID route helper for the native installer."""
import argparse
import ipaddress
import json
import os
import pwd
import re
import shutil
import subprocess

TABLE = "34568"
PRIORITY = "13457"


def main():
    parser = argparse.ArgumentParser()
    parser.add_argument("action", choices=("start", "stop"))
    parser.add_argument("--user", default="natter-gui")
    parser.add_argument("--gateway", required=True)
    parser.add_argument("--interface", required=True)
    args = parser.parse_args()
    gateway = ipaddress.IPv4Address(args.gateway)
    if not re.fullmatch(r"[A-Za-z0-9_.:-]{1,32}", args.interface):
        parser.error("Invalid interface")
    if os.geteuid() != 0:
        parser.error("This helper requires root")
    uid = str(pwd.getpwnam(args.user).pw_uid)
    if uid == "0":
        parser.error("A dedicated non-root account is required")
    ip = shutil.which("ip")
    if not ip:
        parser.error("iproute2 is required")
    uidrange = uid + "-" + uid
    def run(*words, check=True):
        return subprocess.run([ip, *words], check=check, text=True, capture_output=True)
    rules = run("rule", "show").stdout.splitlines()
    existing = [r for r in rules if r.startswith(PRIORITY + ":")]
    if existing and (len(existing) != 1 or "uidrange " + uidrange + " lookup " + TABLE not in existing[0]):
        parser.error("The reserved routing priority is in use")
    if args.action == "stop":
        if existing:
            run("rule", "del", "pref", PRIORITY, "uidrange", uidrange, "lookup", TABLE)
            run("route", "flush", "table", TABLE)
        return
    routes = run("route", "show", "table", TABLE, check=False)
    if not existing and routes.stdout.strip():
        parser.error("The reserved routing table is in use")
    addresses = json.loads(run("-j", "-4", "addr", "show", "dev", args.interface).stdout)
    networks = [(ipaddress.IPv4Interface(a["local"] + "/" + str(a["prefixlen"])))
                for device in addresses for a in device.get("addr_info", []) if a["family"] == "inet"]
    source = next((a for a in networks if gateway in a.network), None)
    if source is None:
        parser.error("Gateway must be in the interface's connected IPv4 subnet")
    run("route", "replace", "table", TABLE, str(source.network), "dev", args.interface, "src", str(source.ip))
    run("route", "replace", "table", TABLE, "default", "via", str(gateway), "dev", args.interface)
    if not existing:
        try:
            run("rule", "add", "pref", PRIORITY, "uidrange", uidrange, "lookup", TABLE)
        except Exception:
            run("route", "flush", "table", TABLE, check=False)
            raise


if __name__ == "__main__":
    main()

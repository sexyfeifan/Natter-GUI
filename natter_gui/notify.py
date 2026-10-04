#!/usr/bin/env python3
"""Invoked by upstream -e, with its five documented positional arguments."""
import datetime
import json
import os
from pathlib import Path
import sys


def main():
    protocol, target_ip, target_port, public_ip, public_port = sys.argv[1:]
    directory = Path(os.environ["NATTER_GUI_WORKER_DIR"])
    data = {"protocol": protocol, "target_ip": target_ip, "target_port": int(target_port), "public_ip": public_ip,
            "public_port": int(public_port), "updated_at": datetime.datetime.now(datetime.timezone.utc).isoformat()}
    temp = directory / "mapping.tmp"
    temp.write_text(json.dumps(data, ensure_ascii=False), encoding="utf-8")
    os.chmod(temp, 0o600)
    temp.replace(directory / "mapping.json")


if __name__ == "__main__":
    main()

"""Extract user@host lines from a CloudLab/Emulab experiment manifest (XML)."""

from __future__ import annotations

import argparse
import sys
import xml.etree.ElementTree as ET
from collections import defaultdict
from pathlib import Path
from typing import Dict, List, Optional


def _local_tag(tag: str) -> str:
    if tag.startswith("{") and "}" in tag:
        return tag.split("}", 1)[1]
    return tag


def _parse_manifest(path: Path, ssh_user: Optional[str]) -> List[str]:
    try:
        tree = ET.parse(path)
    except ET.ParseError as e:
        raise SystemExit(f"Invalid XML in {path}: {e}") from e

    root = tree.getroot()
    logins_by_host: Dict[str, List[str]] = defaultdict(list)

    for elem in root.iter():
        t = _local_tag(elem.tag)
        if t != "login":
            continue
        h = elem.get("hostname") or elem.get("host")
        if not h:
            continue
        u = elem.get("username") or elem.get("user") or ssh_user
        if not u:
            continue
        if u not in logins_by_host[h]:
            logins_by_host[h].append(u)

    by_host: Dict[str, str] = {}
    for h, users in sorted(logins_by_host.items()):
        if ssh_user:
            if ssh_user in users:
                by_host[h] = ssh_user
            else:
                raise SystemExit(
                    f"No <login username=\"{ssh_user}\"/> for hostname {h!r}. "
                    f"Available usernames: {', '.join(users)}"
                )
        elif len(users) == 1:
            by_host[h] = users[0]
        else:
            raise SystemExit(
                f"Hostname {h!r} has multiple SSH users ({', '.join(users)}). "
                "Pass --ssh-user YOUR_CLOUDLAB_USERNAME to pick one."
            )

    # <host name="..."/> is an alternate DNS name for the same PC as <login hostname>; skip
    # adding those when we already got hosts from <login> (avoids duplicate nodes).
    if not logins_by_host:
        for elem in root.iter():
            t = _local_tag(elem.tag)
            if t == "node":
                h = elem.get("hostname")
            elif t == "host":
                h = elem.get("name") or elem.get("hostname")
            else:
                continue
            if not h or "@" in h or h in by_host:
                continue
            if not ssh_user:
                continue
            by_host[h] = ssh_user

    if not by_host:
        raise SystemExit(
            "No SSH hosts found in manifest. Try --ssh-user if nodes only have bare hostnames."
        )

    lines = sorted(f"{u}@{h}" for h, u in by_host.items())
    return lines


def main(argv: Optional[List[str]] = None) -> int:
    p = argparse.ArgumentParser(description="CloudLab manifest -> user@host lines (stdout or -o).")
    p.add_argument("--manifest", required=True, type=Path, help="Path to downloaded experiment manifest XML")
    p.add_argument(
        "--ssh-user",
        default=None,
        help="Your CloudLab username: required when multiple <login> usernames share a host; "
        "also used for <node>/<host> if there are no <login> rows",
    )
    p.add_argument("-o", "--output", type=Path, default=None, help="Write hosts file (default: print stdout)")
    args = p.parse_args(argv)

    if not args.manifest.is_file():
        raise SystemExit(f"Not a file: {args.manifest}")

    lines = _parse_manifest(args.manifest, args.ssh_user)
    text = "\n".join(lines) + "\n"
    if args.output:
        args.output.parent.mkdir(parents=True, exist_ok=True)
        args.output.write_text(text)
    else:
        sys.stdout.write(text)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())

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


def _pick_user(users: List[str], hostname: str, ssh_user: Optional[str]) -> str:
    if ssh_user:
        if ssh_user not in users:
            raise SystemExit(
                f"No <login username=\"{ssh_user}\"/> for hostname {hostname!r}. "
                f"Available usernames: {', '.join(users)}"
            )
        return ssh_user
    if len(users) == 1:
        return users[0]
    raise SystemExit(
        f"Hostname {hostname!r} has multiple SSH users ({', '.join(users)}). "
        "Pass --ssh-user YOUR_CLOUDLAB_USERNAME to pick one."
    )


def _hosts_in_manifest_node_order(root: ET.Element, ssh_user: Optional[str]) -> List[str]:
    """One line per <node> in document order (matches CloudLab topology node0, node1, …)."""
    lines: List[str] = []
    seen_hosts: set[str] = set()
    for elem in root.iter():
        if _local_tag(elem.tag) != "node":
            continue
        logins_by_host: Dict[str, List[str]] = defaultdict(list)
        for sub in elem.iter():
            if _local_tag(sub.tag) != "login":
                continue
            h = sub.get("hostname") or sub.get("host")
            if not h:
                continue
            u = sub.get("username") or sub.get("user") or ssh_user
            if not u:
                continue
            if u not in logins_by_host[h]:
                logins_by_host[h].append(u)
        if not logins_by_host:
            continue
        if len(logins_by_host) > 1:
            raise SystemExit(
                f"One <node> has multiple login hostnames {list(logins_by_host)!r}; unsupported."
            )
        h, users = next(iter(logins_by_host.items()))
        if h in seen_hosts:
            continue
        u = _pick_user(users, h, ssh_user)
        seen_hosts.add(h)
        lines.append(f"{u}@{h}")
    return lines


def _parse_manifest_legacy_sorted(path: Path, ssh_user: Optional[str]) -> List[str]:
    """Flat <login> scan + sorted hostnames (old behavior)."""
    tree = ET.parse(path)
    root = tree.getroot()
    logins_by_host: Dict[str, List[str]] = defaultdict(list)

    for elem in root.iter():
        if _local_tag(elem.tag) != "login":
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
        by_host[h] = _pick_user(users, h, ssh_user)

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

    return sorted(f"{u}@{h}" for h, u in by_host.items())


def _parse_manifest(path: Path, ssh_user: Optional[str]) -> List[str]:
    try:
        tree = ET.parse(path)
    except ET.ParseError as e:
        raise SystemExit(f"Invalid XML in {path}: {e}") from e

    root = tree.getroot()
    ordered = _hosts_in_manifest_node_order(root, ssh_user)
    if ordered:
        return ordered
    return _parse_manifest_legacy_sorted(path, ssh_user)


def main(argv: Optional[List[str]] = None) -> int:
    p = argparse.ArgumentParser(
        description="CloudLab manifest -> user@host lines (stdout or -o). "
        "Drops the first host (control machine)."
    )
    p.add_argument("--manifest", required=True, type=Path, help="Path to experiment manifest XML")
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
    # First <node> is the control machine; do not use it as generator/workload.
    if lines:
        lines = lines[1:]
    if len(lines) < 2:
        raise SystemExit(
            "Need at least two hosts after dropping the control node "
            "(one generator and one workload)."
        )
    text = "\n".join(lines) + "\n"
    if args.output:
        args.output.parent.mkdir(parents=True, exist_ok=True)
        args.output.write_text(text)
    else:
        sys.stdout.write(text)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())

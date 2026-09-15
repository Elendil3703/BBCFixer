#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""PyPI metadata helper (mechanical input for fetching the library in the Python ecosystem).

Subcommands:
  resolve-old <pkg> <date>   print the latest final release published before the date (YYYY-MM-DD)
                             (the old version of an image harness case: the latest release before
                             the restore date)
  sdist-url   <pkg> <ver>    print the download URL of the sdist (source package) of that version;
                             print nothing and exit 1 if there is no sdist
  repo-url    <pkg> <ver>    print the upstream repository URL (github link found in
                             project_urls / home_page)
  requires    <pkg> <ver>    print the dependency declarations of that version line by line
                             (requires_dist, the equivalent of the POM dependency declarations on
                             the Java side)

Only https://pypi.org/pypi/<pkg>/[<ver>/]json is accessed; caching of the results is up to the caller.
"""
import json
import re
import sys
import urllib.request


def _get(url):
    req = urllib.request.Request(url, headers={"User-Agent": "algo-impl-evidence/1.0"})
    with urllib.request.urlopen(req, timeout=30) as r:
        return json.load(r)


def _is_final(ver):
    """Final releases only: exclude the a/b/rc/dev/post pre-release or post-release markers
    (consistent with the Java side, which compares final releases only)."""
    return not re.search(r"(a|b|rc|dev)\d*$|\.post\d+$", ver)


def _ver_key(ver):
    return [int(x) if x.isdigit() else 0 for x in re.split(r"[.\-+]", ver)]


def resolve_old(pkg, date):
    data = _get(f"https://pypi.org/pypi/{pkg}/json")
    best = None
    for ver, files in data.get("releases", {}).items():
        if not files or not _is_final(ver):
            continue
        first_upload = min(f["upload_time"][:10] for f in files)
        if first_upload <= date:
            if best is None or _ver_key(ver) > _ver_key(best):
                best = ver
    if not best:
        sys.exit(f"!! {pkg} has no final release before {date}")
    print(best)


def sdist_url(pkg, ver):
    data = _get(f"https://pypi.org/pypi/{pkg}/{ver}/json")
    for f in data.get("urls", []):
        if f.get("packagetype") == "sdist":
            print(f["url"]); return
    sys.exit(1)


def repo_url(pkg, ver):
    data = _get(f"https://pypi.org/pypi/{pkg}/{ver}/json")
    info = data.get("info", {})
    cands = list((info.get("project_urls") or {}).values()) + [info.get("home_page") or ""]
    for u in cands:
        if u and "github.com" in u:
            m = re.search(r"github\.com/([\w.-]+/[\w.-]+)", u)
            if m:
                print("https://github.com/" + m.group(1).removesuffix(".git")); return
    sys.exit(1)


def requires(pkg, ver):
    data = _get(f"https://pypi.org/pypi/{pkg}/{ver}/json")
    for r in (data.get("info", {}).get("requires_dist") or []):
        print(r)


if __name__ == "__main__":
    cmd = sys.argv[1]
    {"resolve-old": resolve_old, "sdist-url": sdist_url,
     "repo-url": repo_url, "requires": requires}[cmd](*sys.argv[2:])

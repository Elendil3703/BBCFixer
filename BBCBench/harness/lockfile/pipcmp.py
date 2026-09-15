#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""Compare a rebuilt pip environment against the environment recorded for the case.

`pip freeze` does not echo back the spelling it was given: it normalises names on the
way out, so `pydantic_core==2.14.6` in the recorded file comes back as
`pydantic-core==2.14.6`. Comparing the two files line by line therefore reports a
perfectly good tree as dozens of missing packages, which is a silent wrong answer of
exactly the kind this project keeps tripping over. Names are normalised on both sides
per PEP 503 before anything is compared.

The only difference allowed is the project under test itself, which the recorded file
carries as an `-e git+...` line (stripped before this runs) and the rebuilt tree carries
as an editable install of the local checkout.

usage: pipcmp.py <recorded-freeze> <rebuilt-freeze> <owner/repo>
exit 0 when the two agree, 1 otherwise.
"""
import re
import sys


def norm(name):
    return re.sub(r'[-_.]+', '-', name).lower()


def parse(path):
    out = {}
    for line in open(path, encoding='utf-8', errors='replace'):
        line = line.strip()
        if not line or line.startswith('#') or line.startswith('-e '):
            continue
        if '==' in line:
            n, v = line.split('==', 1)
            out[norm(n.strip())] = v.strip()
        elif ' @ ' in line:                      # name @ file:///... (editable / direct url)
            n = line.split(' @ ', 1)[0]
            out[norm(n.strip())] = 'DIRECT'
        else:
            out[norm(line)] = 'UNVERSIONED'
    return out


def main():
    rec, built, slug = sys.argv[1], sys.argv[2], sys.argv[3]
    a, b = parse(rec), parse(built)
    own = norm(slug.split('/')[-1])

    missing = sorted(k for k in a if k not in b)
    extra = sorted(k for k in b if k not in a)
    moved = sorted(k for k in a if k in b and a[k] != b[k] and b[k] != 'DIRECT')

    problems = []
    if missing:
        problems.append('packages in the pinned listing are not installed: %s' % ', '.join(missing))
    if moved:
        problems.append('version mismatch: %s' % ', '.join(
            '%s pinned %s, installed %s' % (k, a[k], b[k]) for k in moved))
    unexpected = [k for k in extra if k != own]
    if unexpected:
        problems.append('packages installed outside the pinned listing: %s' % ', '.join(unexpected))

    print('pipcmp: %d packages pinned, %d packages rebuilt' % (len(a), len(b)))
    if own in extra:
        print('  ok   project under test %s is installed editable, the only expected difference' % own)
    for p in problems:
        print('  BAD  %s' % p)
    if problems:
        print('PIPCMP_FAIL %d problem(s)' % len(problems))
        sys.exit(1)
    print('PIPCMP_OK the installed dependency tree matches the pinned record')


if __name__ == '__main__':
    main()

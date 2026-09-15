#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""pip form of treecheck.py: what moved between STATE-OLD and STATE-NEW.

The npm side compares two `pkginv.py` inventories of node_modules. On the pip side the
inventory is a `pip freeze --all` listing, so the comparison is by distribution name
rather than by path, and the names have to be normalised per PEP 503 first: the same
distribution is spelled `typing_extensions` in one file and `typing-extensions` in the
other, and comparing the two files literally reports a package that never moved as a
package that appeared and a package that disappeared at once.

Only the trigger library and its own dependency closure may move. The closure is recorded
in pinned/closure-allowed.txt, one distribution name per line. Anything moving outside it
means the upgrade dragged a second dependency with it and the failure can no longer be
attributed to this one upgrade.

The project under test itself is installed editable in both states and is excluded: its
recorded version depends on how pip chooses to spell a local editable install, which is
not a property of the case.

usage: pipdrift.py <tree-OLD> <tree-NEW> <closure-allowed.txt> <owner/repo>
exit 0 when everything that moved lies inside the allowed closure, 1 otherwise.
"""
import os
import re
import sys


def norm(name):
    return re.sub(r'[-_.]+', '-', name).lower()


def parse(path):
    out = {}
    with open(path, encoding='utf-8', errors='replace') as fh:
        for line in fh:
            line = line.strip()
            if not line or line.startswith('#') or line.startswith('-e '):
                continue
            if '==' in line:
                n, v = line.split('==', 1)
                out[norm(n.strip())] = v.strip()
            elif ' @ ' in line:
                out[norm(line.split(' @ ', 1)[0].strip())] = 'DIRECT'
            else:
                out[norm(line)] = 'UNVERSIONED'
    return out


def main():
    old_p, new_p, closure_p, slug = sys.argv[1], sys.argv[2], sys.argv[3], sys.argv[4]
    old, new = parse(old_p), parse(new_p)
    own = norm(slug.split('/')[-1])

    allowed = set()
    if os.path.isfile(closure_p):
        for line in open(closure_p, encoding='utf-8'):
            line = line.strip()
            if line and not line.startswith('#'):
                allowed.add(norm(line))

    rows = []
    bad = 0
    for name in sorted(set(old) | set(new)):
        if name == own:
            continue
        a, b = old.get(name, '-'), new.get(name, '-')
        if a == b:
            continue
        mark = 'IN-CLOSURE' if name in allowed else 'OUTSIDE-CLOSURE'
        if name not in allowed:
            bad += 1
        rows.append('%-40s %-16s -> %-16s %s' % (name, a, b, mark))

    for r in rows:
        print(r)
    print('pipdrift: %d packages moved between STATE-OLD and STATE-NEW, %d of them outside '
          'closure-allowed.txt' % (len(rows), bad))
    if not rows:
        print('PIPDRIFT_FAIL no package moved between the two states; this cannot be a real version swap')
        sys.exit(1)
    if bad:
        print('PIPDRIFT_FAIL')
        sys.exit(1)
    print('PIPDRIFT_OK')


if __name__ == '__main__':
    main()

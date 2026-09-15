#!/usr/bin/env python3
"""Check that two installed-package inventories differ only inside an allowed name set.

Inventories are the output of pkginv.py: one line per installed package,
"<path under node_modules>\t<name>@<version>".

Reports every package whose install location or version changed between the two states,
and fails if any of them is not in the allowed set. The allowed set is the trigger
library plus, when the trigger library carries its own dependencies, that library's
installed dependency closure (see depclosure.py).

usage: treecheck.py <tree-old.txt> <tree-new.txt> <allowed-names.txt>
"""
import sys


def load(path):
    entries = {}
    with open(path, encoding='utf-8') as fh:
        for line in fh:
            line = line.rstrip('\n')
            if not line:
                continue
            loc, spec = line.split('\t', 1)
            at = spec.rfind('@')
            entries[loc] = (spec[:at], spec[at + 1:])
    return entries


def main():
    old = load(sys.argv[1])
    new = load(sys.argv[2])
    with open(sys.argv[3], encoding='utf-8') as fh:
        allowed = {l.strip() for l in fh if l.strip()}

    changed = {}
    for loc in set(old) | set(new):
        o = old.get(loc)
        n = new.get(loc)
        if o == n:
            continue
        for side, val in (('old', o), ('new', n)):
            if val is None:
                continue
            changed.setdefault(val[0], set()).add('%s %s@%s at %s' % (side, val[0], val[1], loc))

    offenders = sorted(set(changed) - allowed)
    print('changed packages (%d): %s' % (len(changed), ', '.join(sorted(changed)) or 'none'))
    for name in sorted(changed):
        mark = 'OK ' if name in allowed else 'OFFENDER '
        for detail in sorted(changed[name]):
            print('  %s%s' % (mark, detail))
    if offenders:
        print('TREECHECK_FAIL: %d package(s) outside the allowed closure changed: %s'
              % (len(offenders), ', '.join(offenders)))
        sys.exit(1)
    print('TREECHECK_OK: every change is inside the allowed closure (%d allowed names)'
          % len(allowed))


if __name__ == '__main__':
    main()

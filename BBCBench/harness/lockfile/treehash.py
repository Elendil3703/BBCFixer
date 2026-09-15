#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""Fingerprint an installed dependency tree, file by file.

The dependency gate used to be an inventory of `name@version` per install location
(pkginv.py). That is the right instrument for the question the case-building harness
asks, which is whether the upgrade moved a package it was not allowed to move. It is
not enough for scoring an Agent, because an Agent can edit a dependency's source in
place and leave every version string exactly where it was. Worse, an installed tree is
outside the project's git repository, so nothing in `git diff` or `git status` shows the
edit either: the change would be invisible to both instruments at once.

This walks the tree and prints one line per entry, `<relative path>\\t<sha256>`, with
symlinks recorded as their target rather than followed. Caches that the test run itself
writes are excluded, because a difference that appears merely because the suite ran twice
is not an Agent's doing and a gate that cries wolf gets switched off.

usage: treehash.py <root> [<root> ...]
"""
import hashlib
import os
import sys

SKIP_DIRS = {'__pycache__', '.cache', '.nyc_output', '.pytest_cache', '.mypy_cache',
             '.git', 'node_modules/.cache'}
SKIP_EXT = {'.pyc', '.pyo'}
SKIP_NAMES = {'.DS_Store'}


def digest(path):
    h = hashlib.sha256()
    with open(path, 'rb') as fh:
        for b in iter(lambda: fh.read(1 << 16), b''):
            h.update(b)
    return h.hexdigest()


def walk(root, label):
    if not os.path.isdir(root):
        return
    for dp, dn, fn in os.walk(root):
        dn[:] = sorted(d for d in dn if d not in SKIP_DIRS)
        for f in sorted(fn):
            if f in SKIP_NAMES or os.path.splitext(f)[1].lower() in SKIP_EXT:
                continue
            p = os.path.join(dp, f)
            rel = '%s/%s' % (label, os.path.relpath(p, root))
            if os.path.islink(p):
                print('%s\tLINK:%s' % (rel, os.readlink(p)))
                continue
            try:
                print('%s\t%s' % (rel, digest(p)))
            except OSError as e:
                print('%s\tUNREADABLE:%s' % (rel, e.errno))


def main():
    for root in sys.argv[1:]:
        walk(root, os.path.basename(root.rstrip('/')) or 'root')


if __name__ == '__main__':
    main()

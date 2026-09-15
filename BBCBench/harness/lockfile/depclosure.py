#!/usr/bin/env python3
"""Compute the installed dependency closure of one package inside a node_modules tree.

Given the tree root and a package name, walk `dependencies` and `optionalDependencies`
of the installed package.json files, resolving each name the way node does (walk up the
node_modules chain from the requiring package). Print every reachable package name,
including the root package itself, one per line.

usage: depclosure.py <node_modules-root> <package-name>
"""
import json
import os
import sys


def read_pkg(path):
    try:
        with open(os.path.join(path, 'package.json'), encoding='utf-8') as fh:
            return json.load(fh)
    except Exception:
        return None


def resolve(nm_root, from_dir, name):
    """Node resolution: from from_dir, walk up looking for node_modules/<name>."""
    cur = os.path.abspath(from_dir)
    root = os.path.abspath(os.path.dirname(nm_root))
    while True:
        cand = os.path.join(cur, 'node_modules', name)
        if os.path.isfile(os.path.join(cand, 'package.json')):
            return cand
        if cur == root or len(cur) <= len(root):
            return None
        parent = os.path.dirname(cur)
        if parent == cur:
            return None
        cur = parent


def main():
    nm_root = sys.argv[1]
    rootname = sys.argv[2]
    start = os.path.join(nm_root, rootname)
    if not os.path.isfile(os.path.join(start, 'package.json')):
        sys.stderr.write('depclosure: %s not installed at top level\n' % rootname)
        sys.exit(2)

    seen_dirs = set()
    names = set()
    stack = [start]
    while stack:
        d = os.path.abspath(stack.pop())
        if d in seen_dirs:
            continue
        seen_dirs.add(d)
        pkg = read_pkg(d)
        if pkg is None:
            continue
        if pkg.get('name'):
            names.add(pkg['name'])
        deps = {}
        deps.update(pkg.get('dependencies') or {})
        deps.update(pkg.get('optionalDependencies') or {})
        for dep in deps:
            target = resolve(nm_root, d, dep)
            if target:
                stack.append(target)
            else:
                # declared but not installed (optional dep skipped); still record the name
                names.add(dep)
    for n in sorted(names):
        print(n)


if __name__ == '__main__':
    main()

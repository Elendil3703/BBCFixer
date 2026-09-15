#!/usr/bin/env python3
"""Inventory every installed package under node_modules: path -> name@version."""
import os, sys, json

root = sys.argv[1]
out = []
for dirpath, dirnames, filenames in os.walk(root):
    base = os.path.basename(dirpath)
    parent = os.path.basename(os.path.dirname(dirpath))
    # a package dir is one whose parent is node_modules, or grandparent is node_modules and parent starts with @
    is_pkg = False
    if parent == 'node_modules' and not base.startswith('@') and not base.startswith('.'):
        is_pkg = True
    elif parent.startswith('@') and os.path.basename(os.path.dirname(os.path.dirname(dirpath))) == 'node_modules':
        is_pkg = True
    if is_pkg:
        pj = os.path.join(dirpath, 'package.json')
        if os.path.isfile(pj):
            try:
                d = json.load(open(pj, encoding='utf-8'))
                rel = os.path.relpath(dirpath, root)
                out.append('%s\t%s@%s' % (rel, d.get('name'), d.get('version')))
            except Exception as e:
                out.append('%s\tPARSE_ERROR' % os.path.relpath(dirpath, root))
for line in sorted(out):
    print(line)

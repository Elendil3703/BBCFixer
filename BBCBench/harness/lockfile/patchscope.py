#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""Patch-landing gate for a rebuilt BBC case.

`reference_fix.patch` is the case's reference repair. Two ways of writing it destroy the case
without producing any error at run time.

  An empty patch.  STATE-FIXED then equals STATE-NEW, and the case only looks sound
  because the three exit codes happen to line up for some other reason.

  A patch that edits the test suite.  Turning the failing assertion green is not a repair
  of the downstream project; it is the fake-fix this whole dataset exists to detect. The
  same applies to a patch that only edits packaging metadata or a checked-in build
  artefact: the first repairs nothing, the second edits generated output rather than the
  source it is generated from.

`rebuild_harness.sh` runs this script before applying reference_fix.patch, so both failure modes
are asserted inside the harness.

Classification of every path the patch touches:

  test          any path segment in TEST_DIRS, or a basename matching a test-file pattern.
                Always fatal. No declaration can lift it, because letting a case declare
                its way into editing tests would empty the gate of its only real content.
  vendored      anything under node_modules. Always fatal.
  generated     a checked-in build artefact: first segment in GENERATED_DIRS, or a
                minified / bundled basename. Fatal UNLESS the case declares that path in
                meta.json. Whether `dist/` counts as production code genuinely differs
                between projects, which is why this is a per-case declaration rather than
                a fixed answer: one project may commit babel output under dist/, while
                another may legitimately ship hand-written code from a directory of that name.
  metadata      packaging and documentation files. Fatal unless declared, same mechanism.
  production    everything else. Accepted.

The declaration lives in meta.json:

    "production_paths": ["dist/"],
    "production_paths_why": "why this path is production code in this project"

A prefix ending in `/` covers a directory, otherwise it must equal the file path exactly.
A justification that is empty or still reads TODO does not count, and a declared prefix
that the patch never touches is itself an error, so the declaration cannot be left behind
as decoration after the patch has moved on.

usage: patchscope.py <case-dir>
"""
import json
import os
import re
import sys

TEST_DIRS = {
    'test', 'tests', '__tests__', '__test__', 'spec', 'specs', '__specs__',
    'testing', 'testsuite', 'test-suite', 'fixture', 'fixtures', '__fixtures__',
    'testdata', 'test-data', 'mocks', '__mocks__', 'e2e', 'integration-tests',
    'benchmark', 'benchmarks', 'perf',
}
TEST_FILE = re.compile(
    r'(^|[._-])(test|tests|spec|specs)([._-]|$)'
    r'|^(test|spec)[._-]'
    r'|[._-](test|spec)\.[A-Za-z0-9]+$',
    re.I)
GENERATED_DIRS = {
    'dist', 'build', 'out', 'output', 'bundle', 'bundles', 'umd', 'esm', 'cjs',
    'amd', 'coverage', '.nyc_output', 'vendor', 'min', 'compiled', 'generated',
}
GENERATED_FILE = re.compile(r'\.(min|bundle|pack)\.[A-Za-z0-9]+$', re.I)
METADATA_FILES = {
    'package.json', 'package-lock.json', 'npm-shrinkwrap.json', 'yarn.lock',
    'bower.json', '.npmignore', '.gitignore', '.travis.yml', 'appveyor.yml',
    '.editorconfig', '.eslintrc', '.jshintrc', 'makefile', 'gulpfile.js',
    'gruntfile.js',
    # pip packaging and project configuration, same nature as package.json: not production code
    'setup.py', 'setup.cfg', 'pyproject.toml', 'tox.ini', 'manifest.in',
    'requirements.txt', 'requirements-dev.txt', 'poetry.lock', 'pipfile',
    'pipfile.lock', '.pre-commit-config.yaml',
}
METADATA_EXT = {'.md', '.markdown', '.rst', '.txt', '.yml', '.yaml', '.lock'}
METADATA_STEMS = {'license', 'licence', 'readme', 'changelog', 'history', 'authors',
                  'contributing', 'notice'}
CODE_EXT = {'.js', '.jsx', '.mjs', '.cjs', '.es6', '.ts', '.tsx', '.coffee', '.cjsx',
            '.litcoffee', '.vue', '.svelte',
            # pip form. Without these extensions every Python case would fail the check that
            # at least one accepted file carries a source-code extension.
            '.py', '.pyi', '.pyx', '.pxd'}
PLACEHOLDER = 'TODO'


def parse_patch(text):
    """Paths the patch writes to, plus the number of changed content lines."""
    paths, changed, hunks = set(), 0, 0
    for line in text.splitlines():
        if line.startswith('+++ ') or line.startswith('--- '):
            p = line[4:].split('\t')[0].strip()
            if p and p != '/dev/null':
                if p[:2] in ('a/', 'b/'):
                    p = p[2:]
                paths.add(p)
            continue
        if line.startswith('diff --git '):
            continue
        if line.startswith('@@'):
            hunks += 1
            continue
        if line.startswith('rename to ') or line.startswith('copy to '):
            paths.add(line.split(' to ', 1)[1].strip())
            continue
        if line[:1] in ('+', '-'):
            changed += 1
    return sorted(paths), changed, hunks


def classify(path):
    segs = [s for s in path.replace('\\', '/').split('/') if s and s != '.']
    if not segs:
        return 'metadata', 'no path'
    base = segs[-1]
    low = [s.lower() for s in segs]
    if 'node_modules' in low:
        return 'vendored', 'lies under node_modules'
    for s in low[:-1]:
        if s in TEST_DIRS:
            return 'test', 'directory segment %r is a test directory' % s
    if base.lower() in ('test.js', 'spec.js', 'conftest.py') or TEST_FILE.search(base):
        return 'test', 'file name %r reads as a test file' % base
    for s in low[:-1]:
        if s in GENERATED_DIRS:
            return 'generated', 'lies under the build-output directory %r' % s
    if GENERATED_FILE.search(base):
        return 'generated', 'file name %r is a minified or bundled artefact' % base
    stem, ext = os.path.splitext(base)
    if base.lower() in METADATA_FILES or stem.lower() in METADATA_STEMS \
            or ext.lower() in METADATA_EXT:
        return 'metadata', 'is packaging or documentation, not code'
    if low[0] == '.github':
        return 'metadata', 'is repository configuration, not code'
    return 'production', 'production code'


def declared(path, prefixes):
    for p in prefixes:
        if p.endswith('/'):
            if path == p.rstrip('/') or path.startswith(p):
                return p
        elif path == p:
            return p
    return None


def main():
    case_dir = sys.argv[1].rstrip('/')
    patch_path = os.path.join(case_dir, 'reference_fix.patch')
    meta = json.load(open(os.path.join(case_dir, 'meta.json'), encoding='utf-8'))
    name = meta.get('name', os.path.basename(case_dir))
    prefixes = [str(p) for p in (meta.get('production_paths') or [])]
    why = str(meta.get('production_paths_why') or '').strip()

    problems, notes = [], []

    if not os.path.isfile(patch_path):
        print('PATCHSCOPE_FAIL %s: no reference_fix.patch' % name)
        sys.exit(1)
    text = open(patch_path, encoding='utf-8', errors='replace').read()
    paths, changed, hunks = parse_patch(text)

    if not text.strip():
        problems.append('reference_fix.patch is empty')
    if hunks == 0:
        problems.append('reference_fix.patch carries no hunk')
    if changed == 0:
        problems.append('reference_fix.patch changes no line')
    if not paths:
        problems.append('reference_fix.patch names no target file')

    if prefixes and (not why or why.upper() == PLACEHOLDER):
        problems.append('production_paths is declared with no justification in meta.json')

    accepted, used = [], set()
    for p in paths:
        kind, reason = classify(p)
        cover = declared(p, prefixes)
        if kind in ('test', 'vendored'):
            problems.append('%s is not production code: %s' % (p, reason))
            if cover:
                problems.append('%s may not be lifted by production_paths %r' % (p, cover))
        elif kind in ('generated', 'metadata'):
            if cover and why and why.upper() != PLACEHOLDER:
                used.add(cover)
                accepted.append(p)
                notes.append('%s %s, accepted under declared production path %r'
                             % (p, reason, cover))
            else:
                problems.append('%s %s, and meta.json does not declare it as production '
                                'code for this project' % (p, reason))
        else:
            accepted.append(p)
            notes.append('%s is production code' % p)

    for p in prefixes:
        if p not in used:
            problems.append('production_paths declares %r, which this reference_fix.patch never '
                            'touches; re-record' % p)

    if accepted and not any(os.path.splitext(p)[1].lower() in CODE_EXT for p in accepted):
        problems.append('no accepted file carries a source-code extension')

    print('patchscope: case=%s files=%d hunks=%d changed-lines=%d'
          % (name, len(paths), hunks, changed))
    for n in notes:
        print('  ok   %s' % n)
    if problems:
        for p in problems:
            print('  BAD  %s' % p)
        print('PATCHSCOPE_FAIL %s: %d problem(s)' % (name, len(problems)))
        sys.exit(1)
    print('PATCHSCOPE_OK %s: %d file(s), all production code' % (name, len(accepted)))


if __name__ == '__main__':
    main()

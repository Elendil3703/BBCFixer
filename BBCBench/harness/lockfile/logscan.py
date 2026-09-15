#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""Answer-leakage gate for the three-state logs archived with a rebuilt BBC case.

The archived STATE-OLD / STATE-NEW / STATE-FIXED logs are the record of what a repair
Agent sees when it is handed the broken state. If those logs name the trigger library or
the offending method, the task's whole diagnostic difficulty is given away. This is the
same class of integrity defect as the case-comment leak found in the reserve harness.

Two kinds of mention must be told apart.

  Harness-attributable.  Introduced by us, never by the project under test: the case name
  anywhere in the log, or an answer-bearing token reaching the log through the harness
  workspace path (token and path on the same line). These are always a defect and fail the
  gate unconditionally. A neutral harness path on its own is not a defect, which is why the
  workspace directory is named from a hash of the case name rather than from the case name.

  Genuine.  Actually produced by the project under test, for example a stack frame that
  descends into the trigger library, or a configuration warning the library itself prints.
  Removing these would falsify the case, so they are allowed, but only when they have been
  looked at and justified. Each (log, token) pair must appear in pinned/log-mentions.txt
  with a non-placeholder justification, and its count must match exactly. An unrecorded
  mention, a changed count, or a justification still reading TODO all fail the gate.

usage: logscan.py <case-dir> [--record] [--workroot <path-fragment>]
"""
import json
import os
import re
import sys

STATE_LOGS = ('STATE-OLD.log', 'STATE-NEW.log', 'STATE-FIXED.log')
RECORD = 'log-mentions.txt'
PLACEHOLDER = 'TODO'
# segments that carry no answer content, dropped when deriving tokens from the case name
STOPWORDS = {
    'in', 'to', 'of', 'the', 'a', 'an', 'and', 'or', 'not', 'no', 'is', 'by', 'on',
    'default', 'defaults', 'test', 'tests', 'sync', 'async', 'arg', 'args', 'flag',
    'curried', 'status', 'send', 'iteratee', 'thisarg',
}


def derive_tokens(meta):
    """Tokens whose appearance in a STATE log would hand the Agent the answer."""
    name = meta['name']
    lib = (meta.get('lib') or '').strip()
    repo = (meta.get('repo') or '')
    owner, _, reponame = repo.partition('/')
    # everything that merely identifies the downstream project is not an answer
    benign = set()
    for part in re.split(r'[^A-Za-z0-9]+', (owner + ' ' + reponame).lower()):
        if part:
            benign.add(part)

    tokens = set()
    if lib:
        tokens.add(lib.lower())
    for part in re.split(r'[^A-Za-z0-9]+', name.lower()):
        if not part or part in benign or part in STOPWORDS or len(part) < 3:
            continue
        tokens.add(part)
    for extra in meta.get('sensitive_tokens') or []:
        tokens.add(str(extra).lower())
    return name, sorted(tokens)


def load_record(path):
    rec = {}
    if not os.path.isfile(path):
        return rec
    with open(path, encoding='utf-8') as fh:
        for line in fh:
            line = line.rstrip('\n')
            if not line or line.startswith('#'):
                continue
            parts = line.split('\t')
            if len(parts) < 4:
                continue
            log, token, count, why = parts[0], parts[1], parts[2], '\t'.join(parts[3:])
            rec[(log, token)] = (int(count), why.strip())
    return rec


def main():
    case_dir = sys.argv[1].rstrip('/')
    record_mode = '--record' in sys.argv
    workroot = None
    if '--workroot' in sys.argv:
        workroot = sys.argv[sys.argv.index('--workroot') + 1]

    meta = json.load(open(os.path.join(case_dir, 'meta.json'), encoding='utf-8'))
    name, tokens = derive_tokens(meta)
    pinned = os.path.join(case_dir, 'pinned')
    rec_path = os.path.join(pinned, RECORD)
    recorded = load_record(rec_path)

    fatal = []
    observed = {}
    scanned = []

    for log in STATE_LOGS:
        path = os.path.join(pinned, log)
        if not os.path.isfile(path):
            fatal.append('missing archived log %s' % log)
            continue
        scanned.append(log)
        text = open(path, encoding='utf-8', errors='replace').read()
        low = text.lower()

        # --- harness-attributable: always fatal ---
        n = low.count(name.lower())
        if n:
            fatal.append('%s contains the case name %d time(s)' % (log, n))
        # A neutral harness path in the log is harmless in itself. What is never allowed is
        # an answer-bearing token reaching the log THROUGH that path, i.e. on the same line.
        if workroot:
            for ln in low.splitlines():
                if workroot.lower() not in ln:
                    continue
                for tok in tokens:
                    if tok in ln:
                        fatal.append('%s leaks %r through the harness workspace path: %s'
                                     % (log, tok, ln.strip()[:160]))

        # --- genuine mentions: must be accounted for ---
        for tok in tokens:
            c = low.count(tok)
            if c:
                observed[(log, tok)] = c

    if record_mode:
        os.makedirs(pinned, exist_ok=True)
        keep = load_record(rec_path)
        with open(rec_path, 'w', encoding='utf-8') as fh:
            fh.write('# Mentions of answer-bearing tokens left in the archived STATE logs.\n')
            fh.write('# Every line must be justified as genuine output of the project under\n')
            fh.write('# test; a justification still reading %s fails the gate.\n' % PLACEHOLDER)
            fh.write('# log\ttoken\tcount\tjustification\n')
            for (log, tok), c in sorted(observed.items()):
                why = keep.get((log, tok), (None, PLACEHOLDER))[1] or PLACEHOLDER
                fh.write('%s\t%s\t%d\t%s\n' % (log, tok, c, why))
        print('logscan: recorded %d mention(s) into %s' % (len(observed), rec_path))

    problems = list(fatal)
    for key, c in sorted(observed.items()):
        if key not in recorded and not record_mode:
            problems.append('%s mentions %r %d time(s), not accounted for in %s'
                            % (key[0], key[1], c, RECORD))
        elif key in recorded:
            rc, why = recorded[key]
            if rc != c:
                problems.append('%s mentions %r %d time(s), %s records %d'
                                % (key[0], key[1], c, RECORD, rc))
            if not why or why.upper() == PLACEHOLDER:
                problems.append('%s / %r has no justification in %s' % (key[0], key[1], RECORD))
    for key in sorted(recorded):
        if key not in observed:
            problems.append('%s records a mention of %r that no longer occurs; re-record'
                            % (key[0], key[1]))

    print('logscan: case=%s tokens=%s logs=%s' % (name, ','.join(tokens), ','.join(scanned)))
    for (log, tok), c in sorted(observed.items()):
        why = recorded.get((log, tok), (None, ''))[1]
        print('  mention %-16s %-18s x%-3d %s' % (log, tok, c, why or '(unjustified)'))
    if problems:
        for p in problems:
            print('  LEAK %s' % p)
        print('LOGSCAN_FAIL: %d problem(s)' % len(problems))
        sys.exit(1)
    print('LOGSCAN_OK: no harness-attributable leakage; %d genuine mention(s) justified'
          % len(observed))


if __name__ == '__main__':
    main()

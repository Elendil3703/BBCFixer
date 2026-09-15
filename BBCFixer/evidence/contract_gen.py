#!/usr/bin/env python3
"""Repair contract generator (the contract component; shared by both ecosystems).

Mechanically assembles two independent lines of evidence into a "repair contract" (contract.md) whose
items can be checked one by one:
  - the declared channel: the paired assertion changes (old expectation -> new expectation) in the
    sliced test diff of the library; this is the "correct behavior after the upgrade" written down by
    the library maintainers themselves;
  - the measured channel: the diverging boundary calls (old return -> new return) found by differential
    execution, taken from behavior-diff.json.

Contract items come in three kinds:
  C items (declared contract): assertion expectation changes from the test diff;
  D items (measured contract): diverging calls from differential execution. The fix must land on the
      downstream side (no change to the library implementation, no wrapping, no monkey patching, no
      dependency downgrade). No uniform rule is imposed on the return shape of the call after the repair:
      for return-value handling breakages it should agree with v_new; for input/configuration protocol
      breakages, restoring the old business result by adapting through the configuration mechanisms the
      library exposes is in fact the sign of a successful repair. The criterion is where the fix lands,
      not what the return value looks like;
  T items (judging contract): the judging tests must pass under the original assertions (or those frozen
      after the reference fix).

This file only does mechanical extraction and template assembly; it contains no case knowledge and no
model calls. Parts that cannot be extracted are left empty as is and handed to the reviewer
(reviewer.py) for human-level judgment at acceptance.

Usage:
  contract_gen.py --evidence-dir EV --out EV/contract.md \
      [--behavior-json EV/behavior-diff.json] [--symptom symptom.log] \
      [--eco python|java] [--pkg NAME] [--old OLD_VERSION] [--new NEW_VERSION]
"""
import argparse
import json
import os
import re

ASSERT_PAT = {
    "python": re.compile(r"\bassert\b|assertEqual|assertAlmostEqual|assertRaises|pytest\.raises|\.equals\(|== |!= "),
    "java": re.compile(r"assert(Equals|True|False|That|Null|NotNull|Throws|Same)|Assert\.|Assertions\.|expected\s*="),
    # On the JavaScript side all three major styles are recognized: node's built-in assert (assert.deepEqual /
    # strictEqual), chai/jest's expect(...).to... and .toBe/.toEqual, tape's and nodeunit's t.equal /
    # test.deepEqual, plus the should style. Bare === / !== also count, since assertions are often written
    # directly as comparisons.
    # Two more styles: ava's t.is / t.true / t.regex family (the library tests of decamelize use only t.is;
    # without it not a single assertion in the slice would pair up and the C items would be misreported as
    # zero), and expectType of tsd type tests (the expectType<...>(...) in *.test-d.ts is exactly the correct
    # usage of the new signature).
    "javascript": re.compile(
        r"\bassert\b|assert\.|\bexpect\s*\(|\.to\.|\.should\b|should\.|"
        r"\b(?:deepEqual|deepStrictEqual|strictEqual|notEqual|notDeepEqual|equal|equals|ok|throws|ifError)\s*\(|"
        r"\.(?:toBe|toEqual|toStrictEqual|toMatch|toThrow|toHaveBeenCalled\w*)\s*\(|"
        r"\bt\.(?:is|not|true|false|truthy|falsy|regex|notRegex|like|throwsAsync|notThrows|notThrowsAsync)\s*\(|"
        r"\bexpectType\b|"
        r"===|!==|\bexpected\b"),
}
HUNK_RE = re.compile(r"^@@ .*@@")
FILE_RE = re.compile(r"^\+\+\+ (?:b/)?(.*)$")


def pair_assert_changes(test_diff_path, eco, cap=12):
    """Pair up (old assertion line, new assertion line, file) from the sliced test diff.

    Rule: within the same hunk, deleted and added lines that both match the assertion pattern are paired
    one to one in order of appearance; added assertion lines without a deleted counterpart count on their
    own as "added expectations". Purely mechanical; inaccurate pairings are left to the reviewer."""
    if not os.path.isfile(test_diff_path):
        return [], []
    pat = ASSERT_PAT[eco]
    pairs, added_only = [], []
    cur_file, minus, plus = "?", [], []

    def flush():
        n = min(len(minus), len(plus))
        for i in range(n):
            pairs.append((minus[i], plus[i], cur_file))
        for p in plus[n:]:
            added_only.append((p, cur_file))
        minus.clear(); plus.clear()

    for line in open(test_diff_path, encoding="utf-8", errors="replace"):
        line = line.rstrip("\n")
        m = FILE_RE.match(line)
        if m:
            flush(); cur_file = m.group(1); continue
        if HUNK_RE.match(line):
            flush(); continue
        if line.startswith("-") and not line.startswith("---") and pat.search(line):
            minus.append(line[1:].strip())
        elif line.startswith("+") and not line.startswith("+++") and pat.search(line):
            plus.append(line[1:].strip())
    flush()
    return pairs[:cap], added_only[:cap]


def failing_tests_from_symptom(symptom_path, cap=6):
    if not symptom_path or not os.path.isfile(symptom_path):
        return []
    text = open(symptom_path, encoding="utf-8", errors="replace").read()
    out = set()
    for m in re.finditer(r"FAILED\s+(\S+)", text):
        out.add(m.group(1).rstrip(","))
    for m in re.finditer(r"(\S+Test)\s*[.#(]", text):
        out.add(m.group(1))
    return sorted(out)[:cap]


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--evidence-dir", required=True)
    ap.add_argument("--out", required=True)
    ap.add_argument("--behavior-json")
    ap.add_argument("--symptom")
    ap.add_argument("--eco", choices=["python", "java", "javascript"], default="python")
    ap.add_argument("--pkg", default="the upgraded library")
    ap.add_argument("--old", default="v_old")
    ap.add_argument("--new", default="v_new")
    ns = ap.parse_args()

    pairs, added_only = pair_assert_changes(
        os.path.join(ns.evidence_dir, "test-diff.txt"), ns.eco)

    bj = {}
    bj_path = ns.behavior_json or os.path.join(ns.evidence_dir, "behavior-diff.json")
    if os.path.isfile(bj_path):
        try:
            bj = json.load(open(bj_path, encoding="utf-8"))
        except Exception:
            bj = {}
    divergent = bj.get("divergent_calls", [])
    failing = failing_tests_from_symptom(ns.symptom)

    L = []
    L.append("# Repair contract (%s %s -> %s)" % (ns.pkg, ns.old, ns.new))
    L.append("")
    L.append("> Generated mechanically. C items come from the library test diff (new expectations written by the library maintainers),")
    L.append("> D items from differential execution (return-value differences of boundary calls between v_old and v_new), and the T item from the judging rule.")
    L.append("> Check every relevant item before declaring the repair complete; each item says how it is checked.")
    L.append("")
    n = 0

    if pairs or added_only:
        L.append("## Declared contract (from the library test diff)")
        L.append("")
        for old_l, new_l, f in pairs:
            n += 1
            L.append("**C%d** (%s)" % (n, f))
            L.append("- The library test expectation changed from `%s` to `%s`." % (old_l[:160], new_l[:160]))
            L.append("- Contract: where this behavior is involved, the repaired downstream project must meet the new expectation; do not force the output back to the old one.")
            L.append("- Check: a reviewer compares the agent patch with the judge output.")
            L.append("")
        for new_l, f in added_only:
            n += 1
            L.append("**C%d** (%s, new expectation)" % (n, f))
            L.append("- The library added the test expectation `%s` (no corresponding assertion in the old version)." % new_l[:160])
            L.append("- Contract: the related behavior of the downstream project must be compatible with this new expectation.")
            L.append("- Check: a reviewer decides whether this expectation touches the break, and checks it if so.")
            L.append("")
    else:
        L.append("## Declared contract (from the library test diff)")
        L.append("")
        L.append("(No changed assertion expectation was paired mechanically in the sliced test diff: the library may not have changed assertions here, or the change is outside the slice.")
        L.append("This is a known limitation of the test diff as a source; take the measured D items as the behavior expectation.)")
        L.append("")

    L.append("## Measured contract (from differential execution)")
    L.append("")
    if divergent:
        for d in divergent[:10]:
            n += 1
            L.append("**D%d**" % n)
            L.append("- The boundary call `%s` returns `%s` at v_old and `%s` at v_new (call site %s)."
                     % (d.get("fn"), d.get("ret_old"), d.get("ret_new"), d.get("caller")))
            L.append("- Contract: the environment stays at v_new and the repair must lie on the downstream side: do not modify the "
                     "library implementation, do not wrap, monkey-patch or replace the library locally, and do not downgrade. "
                     "The return value of this call after the repair depends on the kind of break: if the downstream project "
                     "must handle a new return form that is itself correct, the call should return the same as at"
                     " v_new; if the downstream project adapts to a new input or configuration protocol through the library's public "
                     "configuration, arguments or extension points, the call returning the old result again is the sign of a successful repair, not symptom masking.")
            L.append("- Check: mechanical; after the repair the probe reruns the tests and records the return value of this call (observational, probe-report.md); "
                     "the library integrity check is decisive; a reviewer decides the kind of break and where the patch lies.")
            L.append("")
    else:
        L.append("(Differential execution captured no diverging boundary call: the test command may not be pytest, the intact state may have failed to build, or the difference lies inside a C extension.")
        L.append("Rely on the test results and the declared C items.)")
        L.append("")

    L.append("## Judging contract")
    L.append("")
    n += 1
    L.append("**T%d**" % n)
    if failing:
        L.append("- Tests failing in the broken state: %s." % ", ".join("`%s`" % t for t in failing))
    L.append("- Contract: the judged tests must all pass under the original assertions (with the test edits of the reference fix applied); "
             "the tests must actually run (more than zero tests, none skipped); production code must not be hollowed out.")
    L.append("- Check: mechanical, from the exit code and output of the judge.")
    L.append("")

    with open(ns.out, "w", encoding="utf-8") as f:
        f.write("\n".join(L) + "\n")
    print("contract_gen: wrote %s (C items %d, D items %d, T items 1)"
          % (ns.out, len(pairs) + len(added_only), min(len(divergent), 10)))


if __name__ == "__main__":
    main()

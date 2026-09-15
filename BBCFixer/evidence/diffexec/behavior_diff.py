#!/usr/bin/env python3
"""Behavior diff comparator (the comparison component of differential execution, Section 3.2.1; shared by both ecosystems).

Input: two (or three) "run capture directories", each containing:
    test-output.txt   full output of the test run in that environment (required)
    trace.jsonl       boundary call records (present on the Python side; absent on the Java side,
                      in which case the comparison automatically degrades to output only)

Two modes:
  gen mode (evidence collection before the repair): compares the run captures of the v_old and
      v_new environments and writes behavior-diff.md (the measured behavior-difference report
      read by the agent) and behavior-diff.json (the machine-readable version used by
      contract_gen.py).
  probe mode (check after the repair): in addition to the two gen-mode captures, takes "the
      capture of the repaired workspace run in the v_new environment" and records, for each
      diverging call found before the repair, the shape of its return value now. Judging rule:
      the criterion for symptom masking is where the fix lands (whether it rewrites the library
      implementation), not whether the return value equals the old one. Breakages come in two
      types: "return-value handling" (the downstream project should handle the new return shape
      of v_new, so after the repair the call should return what v_new returns) and
      "input/configuration protocol" (the downstream project adapts to the new protocol through
      the configuration, parameters or extension mechanisms the library exposes; after the repair
      the call again yields, under v_new, the same business result as the old version, which is
      exactly the sign of a successful repair). Therefore "returns the old value" is only recorded
      as an observation and is not a verdict on its own; the decisive evidence is the library
      module integrity check (code defined in the downstream project injected into the library
      namespace = hard evidence of monkey patching). Symptom masking is established only when both
      appear together; the remaining cases are left to the reviewer, who judges by where the
      patch lands.

Usage:
  gen:   behavior_diff.py --old-dir A --new-dir B --out X.md --json X.json \
             [--eco python|java] [--label-old ...] [--label-new ...]
  probe: behavior_diff.py --mode probe --old-dir A --new-dir B --fixed-dir C \
             --out probe-report.md [--eco ...]
"""
import argparse
import difflib
import json
import os
import re
import sys

MAX_DIVERGENT = 20   # maximum number of diverging calls listed in the report
MAX_ONLY = 10        # maximum number of one-side-only calls listed


# ---------- reading ----------

def read_output(d):
    p = os.path.join(d, "test-output.txt")
    if not os.path.isfile(p):
        return ""
    return open(p, encoding="utf-8", errors="replace").read()


def read_trace(d):
    p = os.path.join(d, "trace.jsonl")
    recs, meta = [], {}
    if not os.path.isfile(p):
        return recs, meta
    for line in open(p, encoding="utf-8", errors="replace"):
        line = line.strip()
        if not line:
            continue
        try:
            r = json.loads(line)
        except Exception:
            continue
        if r.get("_meta"):
            meta = r
        else:
            recs.append(r)
    return recs, meta


# ---------- test result parsing ----------

_ANSI_RE = re.compile(r"\x1b\[[0-9;]*[A-Za-z]")


def parse_tests(text, eco):
    """Extract (set of failed tests, summary lines, assertion detail lines) from the test output. Best effort; returns empty results when nothing can be parsed."""
    failed, summary, detail = set(), [], []
    if eco == "python":
        for m in re.finditer(r"^(?:FAILED|ERROR)\s+(\S+)", text, re.M):
            failed.add(m.group(1).split(" ")[0])
        for m in re.finditer(r"^=+ .*(passed|failed|error).* =+$", text, re.M):
            summary.append(m.group(0).strip("= ").strip())
        for m in re.finditer(r"^E\s+.*$", text, re.M):
            detail.append(m.group(0).strip())
    elif eco == "javascript":
        # mocha's "N passing / N failing", tape's bare TAP "# pass N", jest's "Tests: ..." and
        # jasmine's "N specs, N failures" are all the same thing: the summary line printed by the test framework itself.
        # Failed test names are taken from mocha's numbered lines and TAP's "not ok" lines.
        for m in re.finditer(r"^\s*\d+\)\s+(.+?)\s*$", text, re.M):
            name = m.group(1).strip()
            if name and not name.startswith("["):
                failed.add(name)
        for m in re.finditer(r"^not ok \d+ (.+)$", text, re.M):
            failed.add(m.group(1).strip())
        for m in re.finditer(r"^.*?(\d+ (?:passing|failing|pending)|\d+ specs?, \d+ failures?|Tests:.*|# (?:tests|pass|fail) +\d+).*$", text, re.M):
            summary.append(m.group(1).strip())
        for m in re.finditer(r"^\s*(?:AssertionError|TypeError|ReferenceError|Error)\b.*$", text, re.M):
            detail.append(m.group(0).strip())
        for m in re.finditer(r"^\s*[+-]\s*(?:expected|actual).*$", text, re.M):
            detail.append(m.group(0).strip())
        # uvu: both the failure lines and the summary line carry ANSI escapes, so none of the regexes above
        # match, and a statement contrary to the facts such as "failed tests in the new environment: (none)"
        # went straight into the evidence given to the agent.
        # Only this small block uses a copy with the escapes stripped; the other branches still read the raw
        # text, so the parsing results of existing cases are unchanged.
        plain = _ANSI_RE.sub("", text)
        for m in re.finditer(r'^\s*FAIL\s+(\S[^"]*?)\s+"(.+?)"\s*$', plain, re.M):
            failed.add("%s > %s" % (m.group(1).strip(), m.group(2).strip()))
        mt = re.search(r"^\s*Total:\s+(\d+)\s*$", plain, re.M)
        mp = re.search(r"^\s*Passed:\s+(\d+)\s*$", plain, re.M)
        if mt and mp:
            summary.append("Total: %s, Passed: %s" % (mt.group(1), mp.group(1)))
    else:  # java (maven/surefire output)
        for m in re.finditer(r"^\[ERROR\]\s+(\S+?)\s+.*<<<\s+(?:FAILURE|ERROR)", text, re.M):
            failed.add(m.group(1))
        for m in re.finditer(r"(\S+)\s*\(([\w.$]+)\)\s+.*<<< (?:FAILURE|ERROR)!", text):
            failed.add("%s.%s" % (m.group(2), m.group(1)))
        for m in re.finditer(r"^Tests run:.*$", text, re.M):
            summary.append(m.group(0).strip())
        for m in re.finditer(r".*(?:expected:.*but was:|ComparisonFailure|AssertionError).*", text):
            detail.append(m.group(0).strip())
    return failed, summary[-3:], detail[:30]


# ---------- boundary call alignment ----------

_ADDR_RE = re.compile(r"0x[0-9a-fA-F]{4,}")
# Since numpy 2.0 the scalar repr changed from `1.0` to `np.float64(1.0)` and from `True` to `np.True_`;
# such differences are only display format, not behavior changes; without normalization they would be
# handed to the agent as the "first divergence".
_NPSCALAR_RE = re.compile(r"\b(?:np|numpy)\.(?:float|int|uint|bool|complex|str|bytes|longlong|half|single|double)\w*\(([^()]*)\)")
_NPBOOL_RE = re.compile(r"\b(?:np|numpy)\.(True_|False_)\b")


def norm_ret(s):
    """Normalization before comparing return values: mask the memory addresses (0x...) in object reprs,
    otherwise the same object necessarily has different addresses in the two runs and a call with no
    difference would be misreported as diverging.
    Also fold the new-style numpy scalar repr back to the bare value (see the comment on _NPSCALAR_RE)."""
    if not isinstance(s, str):
        return s
    s = _ADDR_RE.sub("0xADDR", s)
    for _ in range(3):  # fold several levels when nested
        s2 = _NPSCALAR_RE.sub(r"\1", s)
        if s2 == s:
            break
        s = s2
    s = _NPBOOL_RE.sub(lambda m: m.group(1).rstrip("_"), s)
    return s


def caller_is_downstream(rec):
    """Whether this hop enters the library from the downstream project's own source. Criterion: the full
    path of the call site is inside the workspace (/work or the current directory), not in site-packages,
    and not a synthetic frame such as <frozen ...>/<__array_function__ internals>. Old records without
    caller_path degrade to excluding synthetic frames only. Returns True/False/None (unknown)."""
    cp = rec.get("caller_path") or ""
    c = rec.get("caller") or ""
    if c.startswith("<") or cp.startswith("<"):
        return False
    if not cp:
        return None
    if "/site-packages/" in cp or "/dist-packages/" in cp:
        return False
    if re.match(r"^/usr/(local/)?lib/python", cp) or re.match(r"^/usr/lib64/python", cp):
        return False
    return True


def traceback_files(text):
    """Extract the file names (basename) of the failing stack frames from the pytest output, so that the ranking of diverging calls can prefer "closer to the failure"."""
    names = set()
    for m in re.finditer(r"^(\S+\.py):\d+:", text, re.M):
        names.add(os.path.basename(m.group(1)))
    for m in re.finditer(r'File "([^"]+\.py)", line \d+', text):
        names.add(os.path.basename(m.group(1)))
    return names


def rank_divergent(ddup, tb_files):
    """Ranking: downstream call sites first, then call sites whose file appears in the failing stack frames, then the original order.
    Returns (ranked list, number of downstream diverging calls)."""
    def key(item):
        a = item[0]
        ds = caller_is_downstream(a)
        ds_rank = 0 if ds else (1 if ds is None else 2)
        cb = os.path.basename((a.get("caller") or "").split(":")[0])
        tb_rank = 0 if cb and cb in tb_files else 1
        return (ds_rank, tb_rank, a.get("i", 0))
    ranked = sorted(ddup, key=key)
    has_path = any((it[0].get("caller_path") or "") for it in ddup)
    if has_path:
        n_ds = sum(1 for it in ddup if caller_is_downstream(it[0]))
    else:
        # Old-format records / the JavaScript recorder have no caller_path: fall back to the old behavior, only synthetic frames count as non-downstream.
        n_ds = sum(1 for it in ddup if caller_is_downstream(it[0]) is not False)
    return ranked, n_ds


def norm_args(rec):
    """Normalization before comparing arguments: mask memory addresses as for return values, and keep the key order stable."""
    args = rec.get("args") or {}
    return json.dumps({k: norm_ret(v) for k, v in args.items()}, sort_keys=True)


def align_traces(old_recs, new_recs):
    """Align the two boundary call records by the sequence of call names; returns (diverging pairs, old side only, new side only).

    Alignment uses difflib's longest common subsequence: calls with the same name are paired and their
    return values compared (after address normalization); unpaired calls count as one-side-only (the
    execution path itself diverges)."""
    old_names = [r.get("fn", "?") for r in old_recs]
    new_names = [r.get("fn", "?") for r in new_recs]
    sm = difflib.SequenceMatcher(a=old_names, b=new_names, autojunk=False)
    divergent, only_old, only_new = [], [], []
    for tag, i1, i2, j1, j2 in sm.get_opcodes():
        if tag == "equal":
            for k in range(i2 - i1):
                a, b = old_recs[i1 + k], new_recs[j1 + k]
                # Any of three things differing counts as a divergence:
                #   ret    the return value
                #   alias  reference sharing between the return value and the arguments (a deep clone
                #          degrading to a shallow clone differs only here)
                #   args   the arguments themselves. When the library changes the calling convention of
                #          a callback (async 3 passes one more callback to the test function), the return
                #          values can be identical on both sides and the difference is only in the
                #          arguments; comparing return values alone would miss this kind of breakage entirely.
                if (norm_ret(a.get("ret")), a.get("alias") or "", norm_args(a)) != \
                   (norm_ret(b.get("ret")), b.get("alias") or "", norm_args(b)):
                    divergent.append((a, b))
        else:
            only_old.extend(old_recs[i1:i2])
            only_new.extend(new_recs[j1:j2])
    return divergent, only_old, only_new


def dedup_divergent(divergent):
    """Repeated divergences of the same function keep only the first occurrence (with a count), so that calls in loops do not flood the report."""
    seen, out = {}, []
    for a, b in divergent:
        key = (a.get("fn"), a.get("ret"), b.get("ret"),
               a.get("alias") or "", b.get("alias") or "",
               json.dumps(a.get("args") or {}, sort_keys=True),
               json.dumps(b.get("args") or {}, sort_keys=True))
        if key in seen:
            seen[key] += 1
        else:
            seen[key] = 1
            out.append((a, b, key))
    return [(a, b, seen[k]) for a, b, k in out]


def fmt_args(rec):
    args = rec.get("args") or {}
    if not args:
        return ""
    return ", ".join("%s=%s" % (k, v) for k, v in list(args.items())[:4])


# ---------- gen mode ----------

def run_gen(ns):
    old_out, new_out = read_output(ns.old_dir), read_output(ns.new_dir)
    old_recs, old_meta = read_trace(ns.old_dir)
    new_recs, new_meta = read_trace(ns.new_dir)
    of, osum, _ = parse_tests(old_out, ns.eco)
    nf, nsum, ndetail = parse_tests(new_out, ns.eco)

    divergent, only_old, only_new = align_traces(old_recs, new_recs)
    ddup = dedup_divergent(divergent)
    # Re-rank by "downstream call sites first, closer to the failing stack frames first"; the star mark is only given to divergences in downstream code.
    ddup, n_ds = rank_divergent(ddup, traceback_files(new_out))

    # When the recorder did not attach, the boundary call record is empty; in the output this looks exactly
    # like "the two versions behave identically". The two must be reported separately, otherwise a hook
    # failure would be read as a "no behavior difference" conclusion.
    tracer_error = []
    for lbl, meta in ((ns.label_old, old_meta), (ns.label_new, new_meta)):
        if meta.get("attached") is False:
            tracer_error.append("%s: %s" % (lbl, meta.get("error") or "the boundary-call recorder attached to no library package"))

    j = {
        "tracer_error": tracer_error,
        "label_old": ns.label_old, "label_new": ns.label_new, "eco": ns.eco,
        "failed_old": sorted(of), "failed_new": sorted(nf),
        "newly_failed": sorted(nf - of),
        "assert_detail_new": ndetail,
        "trace_available": bool(old_recs or new_recs),
        "trace_truncated": bool(old_meta.get("truncated") or new_meta.get("truncated")),
        "divergent_calls": [
            {"fn": a.get("fn"), "args": a.get("args") or {},
             "args_old": a.get("args") or {}, "args_new": b.get("args") or {},
             "argc_old": a.get("argc"), "argc_new": b.get("argc"),
             "args_differ": (a.get("args") or {}) != (b.get("args") or {}),
             "ret_old": a.get("ret"), "ret_new": b.get("ret"),
             "alias_old": a.get("alias") or "", "alias_new": b.get("alias") or "",
             "caller": a.get("caller"), "caller_new": b.get("caller"), "times": n,
             "caller_downstream": caller_is_downstream(a)}
            for a, b, n in ddup[:MAX_DIVERGENT]
        ],
        "downstream_divergent_count": n_ds,
        "only_old_calls": [r.get("fn") for r in only_old[:MAX_ONLY]],
        "only_new_calls": [r.get("fn") for r in only_new[:MAX_ONLY]],
    }
    if ns.json_out:
        with open(ns.json_out, "w", encoding="utf-8") as f:
            json.dump(j, f, ensure_ascii=False, indent=1)

    L = []
    L.append("# Measured behavior difference report (differential execution, generated mechanically, no manual judgement)\n")
    L.append("- Intact state: %s\n- Broken state: %s\n" % (ns.label_old, ns.label_new))
    if tracer_error:
        L.append("## 0. The recorder did not work (the call-level evidence of this report does not hold)\n")
        L.extend("- %s" % x for x in tracer_error)
        L.append("\nAn empty record only means that the recorder did not attach; **it does not mean that the two versions behave the same**. Fix the injection before collecting evidence.\n")
    L.append("## 1. Test result differences\n")
    L.append("- Failing tests in the intact state: %s" % ((", ".join(sorted(of)[:8]) or "(none, all pass)")))
    L.append("- Failing tests in the broken state: %s" % ((", ".join(sorted(nf)[:8]) or "(none)")))
    L.append("- Newly failing (caused by the upgrade): %s\n" % ((", ".join(j["newly_failed"][:8]) or "(could not be parsed, see the raw output)")))
    if osum or nsum:
        L.append("Intact-state summary: `%s`; broken-state summary: `%s`\n" % ("; ".join(osum), "; ".join(nsum)))
    if ndetail:
        L.append("## 2. Assertion and exception details in the broken state (first %d lines)\n" % min(len(ndetail), 12))
        L.extend("    %s" % x for x in ndetail[:12])
        L.append("")
    if j["trace_available"]:
        L.append("## 3. Boundary call differences (calls of the same name from the downstream project into the library whose return values differ between the two states)\n")
        if ddup and n_ds == 0:
            L.append("**No return-value difference was observed on the boundary calls of the downstream project's own code.** The divergences listed below all occur on calls into the library "
                     "made from third-party or interpreter-internal frames (such as importlib, array_function dispatch or the internals of other libraries); "
                     "they are of little use for locating the downstream change, so do not treat them as the place of the break. This pattern is common in wholesale upgrades of large foundational libraries: "
                     "the break is a rule-like change (type promotion, str versus bytes, time zones, default arguments) that does not show as a differing return value of a named call; "
                     "rely on the failing assertion and the migration notes instead.\n")
        if ddup and n_ds > 0:
            L.append("**First divergence (the earliest call from the downstream project's own code into the library whose return value differs; the behavior change most likely enters the downstream project here):**\n")
            same_first = None
            for a_, b_, n_ in ddup:
                if (a_.get("args") or {}) == (b_.get("args") or {}):
                    same_first = (a_, b_, n_)
                    break
            if same_first is not None and same_first[0] is not ddup[0][0]:
                L.append("(Among them, the earliest call with **identical arguments in both states** is `%s` (call site %s): "
                         "it returns `%s` in the intact state and `%s` in the broken state. The same arguments giving a different result means that the difference lies in the library implementation itself, "
                         "not in a changed input to the library.)\n"
                         % (same_first[0].get("fn"), same_first[0].get("caller", "?"),
                            same_first[0].get("ret"), same_first[1].get("ret")))
        if ddup:
            for idx, (a, b, n) in enumerate(ddup[:MAX_DIVERGENT]):
                mark = "★ " if (idx == 0 and n_ds > 0) else "- "
                same_args = (a.get("args") or {}) == (b.get("args") or {})
                L.append("%s`%s` (call site %s, %d times)" % (mark, a.get("fn"), a.get("caller", "?"), n))
                if same_args:
                    L.append("    - Same arguments in both states (%d): %s" % (a.get("argc") if a.get("argc") is not None else len(a.get("args") or {}), fmt_args(a)))
                else:
                    L.append("    - Arguments in the intact state (%s): %s" % (a.get("argc"), fmt_args(a)))
                    L.append("    - Arguments in the broken state (%s): %s" % (b.get("argc"), fmt_args(b)))
                L.append("    - Returns in the intact state: `%s`" % a.get("ret"))
                L.append("    - Returns in the broken state: `%s`" % b.get("ret"))
                if (a.get("alias") or "") != (b.get("alias") or ""):
                    L.append("    - The sharing between the return value and the arguments changed: intact state `%s`, broken state `%s`. "
                             "The returned content may be identical field by field but no longer holds the same objects (a deep clone that became a shallow clone is typical)."
                             % (a.get("alias") or "(no sharing)", b.get("alias") or "(no sharing)"))
                if same_args:
                    L.append("    - The same arguments give different return values; the difference comes from the library implementation itself.")
                elif a.get("ret") == b.get("ret"):
                    L.append("    - The return value is the same in both states and only the arguments differ: what this call receives changed"
                             " (this is the pattern when the library changes the calling convention of a callback).")
                else:
                    L.append("    - The arguments already differ, so the divergence lies earlier; this item is its effect propagated to here.")
            L.append("")
        else:
            L.append("(The return values of calls of the same name all agree; the difference may lie in the execution path itself, see below)\n")
        if j["only_old_calls"] or j["only_new_calls"]:
            L.append("Calls reached only in the intact state: %s" % (", ".join("`%s`" % x for x in j["only_old_calls"]) or "(none)"))
            L.append("Calls reached only in the broken state: %s\n" % (", ".join("`%s`" % x for x in j["only_new_calls"]) or "(none)"))
        if j["trace_truncated"]:
            L.append("(Note: the boundary call record hit its limit and was truncated; the above is its first part; rely on the first divergence)\n")
        if ns.eco == "javascript":
            L.append("Recording scope: only the first call from the downstream project's own code into the library package, through both the CommonJS require and "
                     "the ESM import loading paths; calls inside the library and calls through intermediate dependencies are not on this"
                     " path (a stated blind spot).\n")
        else:
            L.append("Recording scope: only the first Python call from the downstream project into the library; C extension functions are not on this path (a stated blind spot).\n")
    else:
        L.append("## 3. Boundary call differences\n(This run produced no call records; only the test output was compared.)\n")
    with open(ns.out, "w", encoding="utf-8") as f:
        f.write("\n".join(L) + "\n")
    print("behavior_diff: wrote %s%s" % (ns.out, (" (%d diverging calls)" % len(ddup)) if ddup else ""))


# ---------- probe mode ----------

def run_probe(ns):
    old_recs, _ = read_trace(ns.old_dir)
    new_recs, _ = read_trace(ns.new_dir)
    fix_recs, fix_meta = read_trace(ns.fixed_dir)
    fix_out = read_output(ns.fixed_dir)
    ff, fsum, fdetail = parse_tests(fix_out, ns.eco)
    monkeypatched = fix_meta.get("monkeypatched") or []

    divergent, _, _ = align_traces(old_recs, new_recs)
    ddup = dedup_divergent(divergent)

    # index of the return value shapes of same-named calls after the repair
    fix_by_fn = {}
    for r in fix_recs:
        fix_by_fn.setdefault(r.get("fn"), []).append(r.get("ret"))

    L = []
    L.append("# Probe report (after the repair, generated mechanically)\n")
    L.append("- The repaired workspace rerun at v_new: failing tests %s; summary `%s`\n"
             % ((", ".join(sorted(ff)[:8]) or "(none)"), "; ".join(fsum)))
    back_to_old = []   # diverging calls that return the old value (observation only; whether it is masking is decided by the integrity check and the reviewer)
    if ddup and fix_recs:
        L.append("## Return values of the pre-repair diverging calls after the repair (observations; the verdict rests on the integrity check and on where the patch lies)\n")
        for a, b, n in ddup[:MAX_DIVERGENT]:
            fn = a.get("fn")
            rets = fix_by_fn.get(fn)
            if not rets:
                L.append("- `%s`: not reached after the repair (the path changed; check manually whether that is reasonable)" % fn)
                continue
            ret_now = rets[0]
            if norm_ret(ret_now) == norm_ret(a.get("ret")) and norm_ret(ret_now) != norm_ret(b.get("ret")):
                back_to_old.append(fn)
                L.append("- `%s`: returns `%s` after the repair, the same as in the intact state. Two possibilities: the downstream project adapted legitimately through the library's public configuration or input protocol"
                         " (restoring the old business result is then the sign of a successful repair), or it wrapped or monkey-patched the library"
                         " (symptom masking). This item is only an observation; see the integrity check below and the reviewer's judgement of where the patch lies." % (fn, ret_now))
            elif norm_ret(ret_now) == norm_ret(b.get("ret")):
                L.append("- `%s`: returns `%s` after the repair (the same as at v_new; the downstream project adapted to the new return form) ✓" % (fn, ret_now))
            else:
                L.append("- `%s`: returns `%s` after the repair (neither the old value nor the pre-repair new value; check manually)" % (fn, ret_now))
        L.append("")
    elif not fix_recs:
        L.append("(The run after the repair produced no call records; only the test results are available.)\n")
    masked = []        # calls where symptom masking is established (returns the old value + library integrity broken, both pieces of evidence present)
    if monkeypatched:
        L.append("## Library module integrity check: **monkey patch found (conclusive)**\n")
        L.append("These library module attributes were replaced by code defined in files of the downstream project; a legitimate repair does not rewrite the library namespace:\n")
        for m in monkeypatched[:10]:
            L.append("- `%s.%s` was replaced by code defined in `%s`" % (m.get("module"), m.get("attr"), m.get("defined_in")))
        L.append("")
        masked = back_to_old[:] or ["(the integrity check found something, but the diverging call records are unavailable)"]
    L.append("## Mechanical conclusion\n")
    L.append("- Tests pass: %s" % ("yes" if not ff and "failed" not in " ".join(fsum) else ("no" if ff else "see summary")))
    L.append("- Monkey-patched library attributes: %d (conclusive; above 0 means that the library implementation was rewritten)" % len(monkeypatched))
    L.append("- Diverging calls returning the old value: %d%s" % (len(back_to_old), (" (%s)" % ", ".join(back_to_old[:5])) if back_to_old else ""))
    if monkeypatched:
        L.append("- **Symptom masking: established** (an old return value and broken library integrity are both present)")
    elif back_to_old:
        L.append("- Symptom masking: not established mechanically (library integrity intact). Returning the old value may be a legitimate adaptation to an input or configuration protocol; "
                 "a reviewer should confirm from where the patch lies that the repair changed only the downstream project's own code.")
    else:
        L.append("- Symptom masking: no sign")
    if fdetail:
        L.append("- Remaining assertion and exception details (first 6 lines):")
        L.extend("    %s" % x for x in fdetail[:6])
    with open(ns.out, "w", encoding="utf-8") as f:
        f.write("\n".join(L) + "\n")
    print("behavior_diff(probe): wrote %s (monkey patches: %d; calls returning the old value, to be judged: %d%s)"
          % (ns.out, len(monkeypatched), len(back_to_old),
             ", symptom masking established" if masked else ""))


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--mode", choices=["gen", "probe"], default="gen")
    ap.add_argument("--old-dir", required=True)
    ap.add_argument("--new-dir", required=True)
    ap.add_argument("--fixed-dir")
    ap.add_argument("--out", required=True)
    ap.add_argument("--json", dest="json_out")
    ap.add_argument("--eco", choices=["python", "java", "javascript"], default="python")
    ap.add_argument("--label-old", default="v_old (intact state)")
    ap.add_argument("--label-new", default="v_new (broken state)")
    ns = ap.parse_args()
    if ns.mode == "probe":
        if not ns.fixed_dir:
            sys.exit("probe mode requires --fixed-dir")
        run_probe(ns)
    else:
        run_gen(ns)


if __name__ == "__main__":
    main()

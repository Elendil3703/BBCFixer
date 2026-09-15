#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""Evidence slicing for BBCFixer (library diff filtering, Section 3.2.2).

Hard rule: **never hand the whole raw diff to the agent**. The source diff and the test diff
between two library versions often run to tens of thousands of lines (lodash 3.10.1 -> 4.17.21:
76000 lines of source diff and 55302 lines of test diff measured); given as is, they bury the
key information and steer the agent towards unrelated changes. This file cuts them down to the
small part that is relevant to the symptom at hand.

**The slicing pipeline reuses the existing implementation in `extract_basis.py`**
instead of keeping an equivalent copy here: stripping licence boilerplate, dropping
pure-reformatting add/delete pairs, line-level focusing, ranking by symptom relevance and
two-hop tracing are all imported directly. These functions are ecosystem-neutral (they only do
line-level operations on unified diff text), so they hold for JavaScript as well; a second copy
would inevitably drift from the main implementation, a class of defect this project has hit
repeatedly.

Entry symbols come from three channels; the order is also the priority:
  Channel 1  measured diverging calls: the function names of the first divergence and of the
             other diverging calls in behavior-diff.json. This is the primary basis for locating
             behavioural breakage, and it is the channel that differential execution
             (Section 3.2.1) adds.
  Channel 2  symptom anchors: distinctive symbols in the round 0 error log (actual values of
             assertions, names of failing tests, stack frames).
  Channel 3  reachability intersection: identifiers that occur in the downstream source and
             also in the changed lines of the library.
             This channel is the noisiest, so it is the only one that goes through the
             `_distinctive` filter for bare generic names.

The first two channels skip the generic-name filter: names such as `max`, `min` or `clone` are
generic, but when one of them is exactly the measured first divergence or the symbol named by
the error, it is precisely the key to slice by; removing it would throw away the most
important clue.

Usage:
  slice_diff.py --eco npm|pip --lib L --old O --new N --src-dir <downstream source root> \
      --error-log <round 0 error log> --source-diff <raw source diff> --test-diff <raw test diff> \
      [--behavior-json behavior-diff.json] --out-dir <evidence dir>
  slice_diff.py --selfcheck        positive and negative self-check
"""
import argparse
import json
import os
import re
import sys

HERE = os.path.dirname(os.path.abspath(__file__))
sys.path.insert(0, HERE)

# The whole slicing pipeline reuses the extract_basis implementation; if any piece is missing,
# fail outright instead of falling back to a local copy.
import extract_basis as EB  # noqa: E402

for _need in ("_strip_boilerplate", "_drop_reformat_pairs", "_focus_to_symbols",
              "_rank_sections_by_relevance", "hop_symbols", "_distinctive",
              "_rank_by_symptom", "parse_error_symbols", "MAX_DIFF_LINES"):
    if not hasattr(EB, _need):
        sys.exit("slice_diff: extract_basis has no %s; refusing to continue with a local copy" % _need)

MAXL = EB.MAX_DIFF_LINES
IDENT = re.compile(r"[A-Za-z_]\w+")

# Language boilerplate words of JavaScript / Python. They are everywhere in a diff; using them
# as slice keys is the same as not slicing.
# Applied only to channel 3 (reachability intersection); the first two channels skip this filter.
LANG_NOISE = {
    "function", "return", "require", "module", "exports", "const", "let", "var",
    "this", "typeof", "instanceof", "undefined", "null", "true", "false", "new",
    "delete", "void", "yield", "async", "await", "class", "extends", "super",
    "prototype", "constructor", "arguments", "callee", "window", "global",
    "process", "console", "Object", "Array", "String", "Number", "Boolean",
    "Function", "Math", "JSON", "Promise", "Error", "TypeError", "RangeError",
    "Symbol", "Map", "Set", "WeakMap", "WeakSet", "Buffer", "Date", "RegExp",
    "import", "from", "def", "self", "cls", "lambda", "elif", "except", "raise",
    "finally", "yield", "None", "True", "False", "print", "range", "len",
    "describe", "it", "before", "after", "beforeEach", "afterEach", "expect",
    "assert", "should", "done", "callback", "err", "error", "result", "value",
    "options", "opts", "args", "props", "params", "config", "context", "target",
    "source", "object", "array", "string", "number", "index", "length", "push",
    "slice", "splice", "concat", "join", "split", "filter", "reduce", "forEach",
    "define", "strict", "use", "type", "types", "test", "tests", "spec",
}

# Boilerplate symbols of the test frameworks and of the node runtime. They always appear in the
# failure output and the stack frames but have nothing to do with the library's behaviour
# change; unless removed they fill the entry-symbol quota and push the real clues out (measured:
# in the lodash case nodeunit, processImmediate, timers and internal took four slots at once).
ERR_NOISE = {
    "nodeunit", "mocha", "jest", "jasmine", "tape", "karma", "chai", "sinon",
    "istanbul", "nyc", "pytest", "unittest", "runTest", "runTests", "runner",
    "processImmediate", "Immediate", "immediate", "timers", "internal",
    "node_modules", "AssertionError", "assertions", "assertion", "deepEqual",
    "equal", "failed", "failure", "failures", "FAILURES", "passing", "pending",
    "Traceback", "Error", "stack", "emit", "emitter", "EventEmitter", "async",
    "bootstrap", "loader", "Module", "compileFunction", "executeUserEntryPoint",
}


def strip_ansi_to(src, dst):  # noqa: D401
    """Strip the terminal colour escape codes from the error log and save a copy; symbol
    extraction reads only that copy.

    The consequence of not stripping was observed in practice: `\x1b[22mFAILURES` is
    extracted as `mFAILURES` and `\x1b[1mintegration` as `mintegration`; these fragments
    have no distinctiveness, yet they occupy entry-symbol slots."""
    if not src or not os.path.isfile(src):
        return ""
    try:
        body = open(src, encoding="utf-8", errors="replace").read()
    except OSError:
        return ""
    body = re.sub(r"\x1b\[[0-9;]*[A-Za-z]", "", body)
    with open(dst, "w", encoding="utf-8") as f:
        f.write(body)
    return dst


def distinctive(s):
    return s not in LANG_NOISE and s not in ERR_NOISE and EB._distinctive(s)


# ---------------------------------------------------------------- entry symbols (three channels)

def syms_from_behavior(bj_path, cap=20):
    """Channel 1: function names of the measured diverging calls (first divergence first).

    The fn in the trace looks like `lodash.max`, `bluebird.promisify()` or `async.whilst#arg1`;
    each dot-separated segment is taken as a symbol, after stripping the tracer's own markers
    `()`, `#argN` and `:pN`."""
    out = []
    if not bj_path or not os.path.isfile(bj_path):
        return out
    try:
        bj = json.load(open(bj_path, encoding="utf-8"))
    except (OSError, ValueError):
        return out
    names = []
    for d in bj.get("divergent_calls") or []:
        names.append(d.get("fn") or "")
    names += [x or "" for x in (bj.get("only_new_calls") or [])]
    names += [x or "" for x in (bj.get("only_old_calls") or [])]
    for fn in names:
        fn = re.sub(r"#arg\d+(?::p\d+)?", "", fn).replace("()", "")
        for part in fn.split("."):
            for m in IDENT.findall(part):
                if m not in out and m not in LANG_NOISE:
                    out.append(m)
    return out[:cap]


def collect_source_ids(src_dir):
    """Identifiers that occur in the downstream source (the symbols the downstream project
    actually uses).

    extract_basis.collect_downstream_ids only recognises .java and .py; nearly all lockfile
    harness cases are JavaScript, so the extension list is widened here and everything else
    stays the same."""
    ids = set()
    if not src_dir or not os.path.isdir(src_dir):
        return ids
    exts = (".js", ".jsx", ".mjs", ".cjs", ".ts", ".coffee", ".py")
    for dp, dns, fns in os.walk(src_dir):
        dns[:] = [d for d in dns
                  if d not in ("node_modules", ".git", ".venv", "build", "dist",
                               "coverage", "__pycache__")]
        for fn in fns:
            if not fn.endswith(exts):
                continue
            try:
                with open(os.path.join(dp, fn), encoding="utf-8", errors="ignore") as f:
                    ids |= set(IDENT.findall(f.read()))
            except OSError:
                pass
    return ids


def changed_line_ids(diff_path, cap_lines=400000):
    """Identifiers that occur in the changed (-/+) lines of the library diff."""
    ids = set()
    if not diff_path or not os.path.isfile(diff_path):
        return ids
    n = 0
    with open(diff_path, encoding="utf-8", errors="replace") as f:
        for line in f:
            n += 1
            if n > cap_lines:
                break
            if line[:1] not in "+-" or line.startswith(("+++", "---")):
                continue
            ids |= set(IDENT.findall(line))
    return ids


MEASURED_QUOTA = 12
ERR_QUOTA = 20


def derive_entry_symbols(bj, error_log, src_dir, source_diff, max_n=40, scratch=None):
    """Merge the three channels, rank by symptom anchoring, cap. Returns (symbol list,
    per-channel detail).

    **The measured channel must keep its slots and must not be pushed out by the cap.** The
    first implementation mixed the three channels, ranked by symptom and truncated; in the
    lodash case the error symbols (including test-framework boilerplate) filled all 40 slots
    and squeezed out the first divergence `max` and `min` entirely, although the first
    divergence is the primary basis for locating behavioural breakage. The measured channel
    therefore gets %d reserved slots at the front, and the remaining slots are assigned by
    symptom-anchored ranking.
    """ % MEASURED_QUOTA
    if scratch:
        # The colour-stripped copy goes to scratch, **not to the evidence directory**: it is
        # only an intermediate product of symbol extraction; putting it into the evidence
        # directory handed to the agent is pointless and would make the evidence list diverge
        # from the whitelist of the gate.
        error_log = strip_ansi_to(error_log, os.path.join(scratch, "error-clean.txt")) or error_log
    err_msg, err_frame = EB.parse_error_symbols(error_log)
    err = []
    for s in err_frame + err_msg:          # stack frames are closer to the throw site; they go before message symbols
        if s in ERR_NOISE or s in LANG_NOISE:
            continue
        if s not in err:
            err.append(s)
    err = err[:ERR_QUOTA]
    measured = [x for x in syms_from_behavior(bj) if x not in ERR_NOISE][:MEASURED_QUOTA]
    down = collect_source_ids(src_dir)
    upch = changed_line_ids(source_diff)
    reach = [s for s in sorted(down & upch) if distinctive(s)]

    rest, seen = [], set(measured)
    for group in (err, reach):
        for s in group:
            if s not in seen:
                seen.add(s)
                rest.append(s)
    rest = EB._rank_by_symptom(rest, err)
    ordered = measured + rest[:max(0, max_n - len(measured))]
    return ordered, {"measured": measured, "error": err,
                     "reachable": reach[:80]}


# ---------------------------------------------------------------- slicing

def make_regex(symbols):
    if not symbols:
        return ""
    return r"\b(" + "|".join(re.escape(s) for s in symbols) + r")\b"


PATH_PER_FILE = 60      # max lines kept per file when matched by file name
PATH_TOTAL = 260        # max total lines of the sections matched by file name
PATH_MAX_FILES = 14
# Quota per tier. Tier 1 (a new sibling method, the maxBy.js form) must get slots: the most
# common mechanism family in this dataset is "the library moves a capability into a new sibling
# method, and the unchanged downstream call degrades silently", and that new method is exactly
# the form the fix needs. Without a reserved quota, the same-name copies of tier 0 push it out
# entirely (measured in the lodash case).
PATH_TIER_QUOTA = {0: 7, 1: 5, 2: 2}


def path_matched_sections(text, path_symbols):
    """Pick out the whole sections whose file name itself matches an entry symbol and put them
    at the front of the slice.

    Why this is needed: line-level focusing (`_focus_to_symbols`) only recognises "a changed
    line contains an entry symbol" and is blind to **entirely new files**: every line of a new
    file is an added line, but the entry symbol itself is not necessarily written in it.
    Libraries published as one file per method (the lodash 4 release artifact has about a
    thousand single-method files) put this behaviour change exactly in a new file named after
    the symbol: the iteratee-taking capability of `_.max` moved into `maxBy.js`. Measured: with
    line focusing alone, the slice of the lodash case never mentioned `maxBy` or `minBy`, which
    are precisely the correct usage after the upgrade.

    **Only the symbols of the measured channel are used for file-name matching** (i.e. the
    library call names recorded by differential execution). Symbols from the error log are
    mostly the downstream project's own domain words (in that case test names such as people,
    transform and random); matching them against library file names would pull in a pile of
    unrelated files. Measured symbols are genuine library entry points and do not.
    """
    low = [x.lower() for x in path_symbols if len(x) >= 3]
    if not low:
        return "", set()
    lines = text.splitlines()
    sections, cur = [], None
    for ln in lines:
        if ln.startswith("+++ "):
            if cur:
                sections.append(cur)
            cur = [ln]
        elif cur is not None:
            cur.append(ln)
    if cur:
        sections.append(cur)
    # Matches fall into three tiers; the earlier the tier, the more likely the file is where
    # this change landed:
    #   tier 0  the file name equals the entry symbol (lodash's max.js)
    #   tier 1  the file name starts with the entry symbol (maxBy.js, i.e. the "new sibling
    #           method" form, the most common mechanism family among the lockfile harness cases)
    #   tier 2  the file name merely contains the symbol (internal pieces such as
    #           _arrayLikeKeys.js, least valuable)
    # Within a tier, shorter sections come first: short sections are information-dense, long
    # ones are mostly internal implementation.
    ranked = []
    for i, sec in enumerate(sections):
        base = os.path.basename(sec[0][4:].split("\t")[0]).lower()
        stem = base.rsplit(".", 1)[0]
        tier = None
        for x in low:
            if stem == x:
                tier = 0; break
            if stem.startswith(x):
                tier = min(tier, 1) if tier is not None else 1
            elif x in stem and tier is None:
                tier = 2
        if tier is None:
            continue
        ranked.append((tier, len(sec), i, sec, stem))
    ranked.sort(key=lambda t: (t[0], t[1], t[2]))
    out, used, taken = [], 0, set()
    per_stem = {}
    per_tier = {}
    for _tier, _ln, _i, sec, stem in ranked:
        if len(taken) >= PATH_MAX_FILES or used >= PATH_TOTAL:
            break
        if per_tier.get(_tier, 0) >= PATH_TIER_QUOTA.get(_tier, 0):
            continue
        # The same file name often occurs several times in the release artifact (lodash's
        # max.js exists in the root, under collection/ and under fp/, with nearly identical
        # content). Keep at most two copies of a name and leave the slots for other files;
        # otherwise the "new sibling method" tier (maxBy.js) is pushed out entirely by the
        # same-name copies.
        if per_stem.get(stem, 0) >= 2:
            continue
        per_stem[stem] = per_stem.get(stem, 0) + 1
        blk = sec[:PATH_PER_FILE]
        if len(sec) > PATH_PER_FILE:
            blk = blk + ["    …（该文件段落超过 %d 行，此处截断）" % PATH_PER_FILE]
        out += blk + [""]
        used += len(blk)
        per_tier[_tier] = per_tier.get(_tier, 0) + 1
        taken.add(sec[0])
    if not out:
        return "", set()
    hdr = ["# 以下段落是【文件名本身命中入口符号】的整份文件改动（按方法拆分发布的库会把",
           "# 一次行为变更整个落在一个以该符号命名的文件里，按行聚焦看不见这种形态）。", ""]
    return "\n".join(hdr + out), taken


def rank_sections_by_density(text, sym_regex, err_regex, symbols):
    """Rank the file sections by **hit density**, densest first, then let the hard cap truncate.

    This ranking rule differs from `_rank_sections_by_relevance` in extract_basis (which ranks
    by hit **count**); the separate rule is justified by measurement, it is not a duplicate:

    npm release artifacts often contain one bundled file with the whole library (lodash's
    index.js has 12351 lines) that is replaced wholesale between major versions. Ranked by hit
    count, that section necessarily beats every other one and eats the whole 600-line cap
    budget, yet as a full rewrite it has no locating value when read line by line; the valuable
    parts are the small per-method files of the same release (maxBy.js and the like), which
    have few hits but very high density. Measured: ranked by count, the slice of the lodash
    case never mentioned maxBy or minBy; index.js pushed them all out.

    score = (3 * error-symbol hits + entry-symbol hits + 5 * file name contains an entry symbol)
    / (section lines + 1).
    The file-name term exists because per-method libraries put the change in a file named after
    the symbol, so the path itself is a signal.
    """
    if not sym_regex:
        return text
    sp = re.compile(sym_regex)
    ep = re.compile(err_regex) if err_regex else None
    lines = text.splitlines()
    titles, sections, cur = [], [], None
    for ln in lines:
        if ln.startswith("# "):
            titles.append(ln); continue
        if ln.startswith("+++ "):
            if cur:
                sections.append(cur)
            cur = [ln]
        elif cur is not None:
            cur.append(ln)
        else:
            titles.append(ln)
    if cur:
        sections.append(cur)
    if len(sections) <= 1:
        return text
    low = [x.lower() for x in symbols]
    scored = []
    for i, sec in enumerate(sections):
        sym_hits = sum(1 for l in sec if sp.search(l))
        err_hits = sum(1 for l in sec if ep and ep.search(l))
        path = sec[0].lower()
        path_hit = 1 if any(x in path for x in low if len(x) >= 3) else 0
        score = (3 * err_hits + sym_hits + 5 * path_hit) / float(len(sec) + 1)
        scored.append((-score, i, sec))
    scored.sort(key=lambda x: (x[0], x[1]))
    out = titles[:]
    for _, _, sec in scored:
        out.extend(sec)
    return "\n".join(out)


def slice_text(raw_path, regex, err_regex, title, hint, cap=None, symbols=None,
               path_symbols=None):
    """Strip licence boilerplate, drop pure-reformatting add/delete pairs, focus by line, rank
    by symptom relevance, apply the hard cap.
    Every step is the existing extract_basis implementation."""
    cap = cap or MAXL
    if not raw_path or not os.path.isfile(raw_path):
        return "（本例没有可用的%s。）\n" % title, 0
    text = open(raw_path, encoding="utf-8", errors="replace").read()
    raw_lines = len(text.splitlines())
    if not regex:
        return ("（求不出入口符号，按硬性规则不落盘整份差异；请在第二阶段定出入口符号后，"
                "用 %s 就地按符号再切。原始差异共 %d 行。）\n" % (hint, raw_lines)), raw_lines
    text = EB._strip_boilerplate(text)
    text = EB._drop_reformat_pairs(text)
    pblock, _ = path_matched_sections(text, path_symbols or [])
    text = EB._focus_to_symbols(text, regex)
    text = rank_sections_by_density(text, regex, err_regex, symbols or [])
    if pblock:
        text = pblock + "\n" + text
    lines = text.splitlines()
    if len(lines) > cap:
        text = "\n".join(lines[:cap]) + (
            "\n\n... [已封顶 %d 行，切片仍偏大（已按症状相关性排序，被截掉的是相关性最低的"
            "段落）。请用更精确的入口符号就地再切。] ...\n" % cap)
    return text, raw_lines


def guide_lines(err_syms, sliced_texts):
    """Evidence guide: for each error symbol, count which evidence sections it hits (pure
    counting, no case knowledge)."""
    out = []
    for s in err_syms[:12]:
        pat = re.compile(r"\b%s\b" % re.escape(s))
        hits = []
        for name, t in sliced_texts:
            n = sum(1 for ln in t.splitlines() if pat.search(ln))
            if n:
                hits.append("%s 命中 %d 行" % (name, n))
        out.append("- `%s`：%s" % (s, "；".join(hits) if hits else "证据里没有命中"))
    return out


# ---------------------------------------------------------------- main flow

def run(ns):
    os.makedirs(ns.out_dir, exist_ok=True)
    scr = ns.scratch or ns.out_dir
    os.makedirs(scr, exist_ok=True)
    syms, detail = derive_entry_symbols(ns.behavior_json, ns.error_log,
                                        ns.src_dir, ns.source_diff, ns.max_symbols,
                                        scratch=scr)
    err_syms = detail["error"]
    regex = make_regex(syms)
    err_regex = make_regex(err_syms)

    code_hint = "diff -ruN 两版发行产物后按符号 grep"
    measured = detail["measured"]
    code_txt, code_raw = slice_text(ns.source_diff, regex, err_regex, "上游源码差异",
                                    code_hint, symbols=syms, path_symbols=measured)
    # One round of two-hop tracing: take the deeper symbols repeatedly touched in the changed
    # lines of the first slice, add them to the slice keys and slice again.
    hops = []
    if regex and os.environ.get("ALGO_HOPS", "1") != "0":
        hops = EB.hop_symbols(code_txt, syms)
        hops = [h for h in hops if h not in LANG_NOISE]
        if hops:
            regex2 = make_regex(syms + hops)
            code_txt, _ = slice_text(ns.source_diff, regex2, err_regex, "上游源码差异",
                                     code_hint, symbols=syms + hops, path_symbols=measured)
            regex = regex2
    test_txt, test_raw = slice_text(ns.test_diff, regex, err_regex, "上游测试差异",
                                    "git diff 两个 tag 的测试目录后按符号 grep",
                                    symbols=syms + hops, path_symbols=measured)

    hdr_code = [
        "# %s %s -> %s 的上游源码差异切片（our 臂证据 · 机械产出）" % (ns.lib, ns.old, ns.new),
        "# 原始差异 %d 行，按入口符号切到以下相关段落。切片管线：剥许可证样板、剔纯排版增删对、"
        "按行聚焦（含符号的增删行上下各 3 行）、按报错符号命中数重排段落、硬上限封顶。" % code_raw,
        "# 入口符号：%s" % ("、".join(syms) or "（未求出）"),
        "# 二跳追加符号：%s" % ("、".join(hops) or "（无）"),
        "",
    ]
    hdr_test = [
        "# %s %s -> %s 的上游测试差异切片（our 臂证据 · 机械产出）" % (ns.lib, ns.old, ns.new),
        "# 原始差异 %d 行。上游测试是维护者亲手写下的「升级后正确用法与新期望值」；"
        "本切片只保留与入口符号相关的段落。" % test_raw,
        "",
    ]
    note = ""
    if ns.test_diff_note and os.path.isfile(ns.test_diff_note):
        note = open(ns.test_diff_note, encoding="utf-8", errors="replace").read().strip()
        if note:
            hdr_test.insert(2, "# 取料说明：%s" % note)

    with open(os.path.join(ns.out_dir, "code-diff-slice.txt"), "w", encoding="utf-8") as f:
        f.write("\n".join(hdr_code) + code_txt + "\n")
    # The file name test-diff.txt is the one contract_gen.py expects; do not change it.
    with open(os.path.join(ns.out_dir, "test-diff.txt"), "w", encoding="utf-8") as f:
        f.write("\n".join(hdr_test) + test_txt + "\n")
    with open(os.path.join(ns.out_dir, "entry-symbols.txt"), "w", encoding="utf-8") as f:
        f.write("实测分叉调用（第一路，第一分叉点排最前）：%s\n" % ("、".join(detail["measured"]) or "（无）"))
        f.write("症状锚点符号（第二路，round 0 报错）：%s\n" % ("、".join(err_syms) or "（无）"))
        f.write("可达性交集（第三路，下游标识符 ∩ 上游变更行标识符，已剔裸通用名）：%s\n"
                % ("、".join(detail["reachable"][:40]) or "（无）"))
        f.write("二跳追加：%s\n" % ("、".join(hops) or "（无）"))
        f.write("最终切片键：%s\n" % ("、".join(syms) or "（无）"))

    g = ["# 证据导读（纯计数，不含案例知识）", "",
         "round 0 报错里的特异符号各自命中了证据的哪些段落：", ""]
    g += guide_lines(err_syms, [("源码差异切片", code_txt), ("测试差异切片", test_txt)])
    with open(os.path.join(ns.out_dir, "evidence-guide.md"), "w", encoding="utf-8") as f:
        f.write("\n".join(g) + "\n")

    print("OUR_SLICE_SYMBOLS=%d" % len(syms))
    print("OUR_SLICE_CODE_RAW=%d" % code_raw)
    print("OUR_SLICE_CODE_KEPT=%d" % len(code_txt.splitlines()))
    print("OUR_SLICE_TEST_RAW=%d" % test_raw)
    print("OUR_SLICE_TEST_KEPT=%d" % len(test_txt.splitlines()))
    print("OUR_SLICE_OK %s" % ns.out_dir)
    return 0


# ---------------------------------------------------------------- self-check

SELF_DIFF = """diff -ruN a/lib/x.js b/lib/x.js
--- a/lib/x.js
+++ b/lib/x.js
@@ -1,20 +1,20 @@
 /*
  * Licensed under the Apache License, Version 2.0
  * unless required by applicable law or agreed to in writing
  */
-function maxBy(coll, iteratee) {
+function maxBy(coll, iteratee, extra) {
   var i = 0;
   return coll;
 }
-  var indented = 1;
+    var indented = 1;
@@ -50,6 +50,6 @@
-function unrelatedHelper(a) {
+function unrelatedHelper(a, b) {
   return a;
 }
"""


def selfcheck():
    import shutil
    import tempfile
    wrong = 0

    def chk(side, label, cond):
        nonlocal wrong
        r = "OK" if cond else "WRONG"
        if not cond:
            wrong += 1
        print("%-4s %-6s %s" % (side, r, label))

    tmp = tempfile.mkdtemp(prefix="ourslice-")
    try:
        raw = os.path.join(tmp, "source-diff.raw")
        open(raw, "w", encoding="utf-8").write(SELF_DIFF)
        err = os.path.join(tmp, "err.log")
        open(err, "w", encoding="utf-8").write("AssertionError: maxBy returned wrong record\n")

        rx = make_regex(["maxBy"])
        errx = make_regex(["maxBy"])
        txt, rawn = slice_text(raw, rx, errx, "源码差异", "工具")
        chk("正例", "切片保留了含入口符号的变更行（maxBy）", "maxBy" in txt)
        chk("正例", "许可证样板被剥掉（Apache License 不出现）", "Apache License" not in txt)
        chk("正例", "纯排版增删对被剔除（indented 那一对不出现）", "indented" not in txt)
        chk("反例", "与入口符号无关的整段被丢弃（unrelatedHelper 不出现）",
            "unrelatedHelper" not in txt)
        chk("反例", "求不出入口符号时绝不落盘整份差异",
            "Apache License" not in slice_text(raw, "", "", "源码差异", "工具")[0])

        # The generic-name filter applies only to channel 3: `max` from the first two channels must survive
        chk("正例", "裸通用名在第三路被剔（distinctive('result') 为假）", not distinctive("result"))
        chk("正例", "特异名在第三路留得住（distinctive('promisifyAll') 为真）",
            distinctive("promisifyAll"))
        bj = os.path.join(tmp, "behavior-diff.json")
        json.dump({"divergent_calls": [{"fn": "lodash.max"}], "only_new_calls": [],
                   "only_old_calls": []}, open(bj, "w", encoding="utf-8"))
        m = syms_from_behavior(bj)
        chk("正例", "实测第一分叉点的裸通用名 max 不被通用名过滤剔掉", "max" in m)

        # Negative: skipping the slicing step entirely (degraded implementation) necessarily keeps unrelated sections and the licence
        degraded = open(raw, encoding="utf-8").read()
        chk("反例", "退化实现（原样给整份差异）会同时留下许可证与无关段落，据此可与正例区分",
            ("Apache License" in degraded) and ("unrelatedHelper" in degraded))

        big = ["+++ b/bundle.js"] + ["-  var v%d = maxBy(x);" % i for i in range(20)] \
            + ["-  var noise%d = 0;" % i for i in range(400)]
        small = ["+++ b/maxBy.js", "+function maxBy(a) {", "+  return a;", "+}"]
        merged = "\n".join(["# t"] + big + small)
        ranked = rank_sections_by_density(merged, make_regex(["maxBy"]), "", ["maxBy"])
        chk("正例", "密度排序把小而对症的 maxBy.js 排到整份重写的 bundle.js 之前",
            ranked.index("+++ b/maxBy.js") < ranked.index("+++ b/bundle.js"))
        by_count = EB._rank_sections_by_relevance(merged, make_regex(["maxBy"]))
        chk("反例", "按命中条数排（退化实现）时 bundle.js 反而排在前面，说明这条排序确有作用",
            by_count.index("+++ b/bundle.js") < by_count.index("+++ b/maxBy.js"))

        newfile = "\n".join(["+++ b/other.js", "-var a = max(coll, cb);", "+var a = 0;"]
                            + [" filler%d" % i for i in range(6)]
                            + ["+++ b/maxBy.js", "+function maxBy(coll, iter) {",
                               "+  return coll;", "+}"])
        pb, _t = path_matched_sections(newfile, ["max"])
        chk("正例", "整份新增的 maxBy.js 靠文件名命中被保留（按行聚焦看不见它）",
            "maxBy.js" in pb)
        chk("反例", "只按行聚焦的退化实现会把整份新增文件整个丢掉",
            "maxBy.js" not in EB._focus_to_symbols(newfile, make_regex(["max"])))
        pb2, _t2 = path_matched_sections(newfile, ["transform"])
        chk("反例", "文件名不含该符号时不会被误留", pb2 == "")
        many = "\n".join(
            ["+++ b/_arrayLikeKeys.js"] + ["+  var q%d = 0;" % i for i in range(50)]
            + ["+++ b/maxBy.js", "+function maxBy(c, i) {", "+  return c;", "+}"])
        pb3, _t3 = path_matched_sections(many, ["keys", "max"])
        chk("正例", "同族新方法（maxBy.js）排在内部件（_arrayLikeKeys.js）之前",
            pb3.index("maxBy.js") < pb3.index("_arrayLikeKeys.js"))
        dup = "\n".join(
            ["+++ b/a/max.js", "+var m1 = 1;"] + ["+++ b/b/max.js", "+var m2 = 1;"]
            + ["+++ b/c/max.js", "+var m3 = 1;"] + ["+++ b/maxBy.js", "+var mb = 1;"])
        pb4, _t4 = path_matched_sections(dup, ["max"])
        chk("正例", "同名副本最多留两份，名额留给 maxBy.js", "maxBy.js" in pb4)
        chk("反例", "第三份同名副本不再占名额", pb4.count("max.js") == 2)
        crowd = []
        for k in range(12):
            crowd += ["+++ b/d%d/max.js" % k, "+var z%d = 1;" % k]
        crowd += ["+++ b/maxBy.js", "+var mb = 1;"]
        pb5, _t5 = path_matched_sections("\n".join(crowd), ["max"])
        chk("正例", "零等的同名副本再多，同族新方法 maxBy.js 仍有名额", "maxBy.js" in pb5)

        # Negative: when entry-symbol derivation ignores the error log, symptom-anchored ranking has no effect
        ordered = EB._rank_by_symptom(["zzz", "maxBy"], ["maxBy"])
        chk("正例", "症状锚定排序把报错点名的符号排到最前", ordered[0] == "maxBy")

        # The measured channel must keep its slots: build an error log full of noise symbols; the first divergence must still be among the keys
        noisy = os.path.join(tmp, "noisy.log")
        open(noisy, "w", encoding="utf-8").write(
            "\n".join("AssertionError at NoiseSymbol%02d.run" % i for i in range(60)))
        syms2, det2 = derive_entry_symbols(bj, noisy, "", raw, max_n=40, scratch=tmp)
        chk("正例", "报错符号再多，第一分叉点（max）也留在最终切片键里", "max" in syms2)
        chk("正例", "实测那一路排在最前", syms2[0] in det2["measured"])
        ansi = os.path.join(tmp, "ansi.log")
        open(ansi, "w", encoding="utf-8").write("\x1b[22mFAILURES: 2/24 \x1b[1mintegration ok\n")
        _, det3 = derive_entry_symbols(bj, ansi, "", raw, max_n=40, scratch=tmp)
        chk("反例", "终端颜色残片不进入入口符号（mFAILURES / mintegration 不出现）",
            not any(x.startswith("m") and x[1:2].isupper() or x in ("mintegration",)
                    for x in det3["error"]))
        # Degraded implementation: without stripping the colour codes the fragments do get in, which proves this self-check is not vacuous
        _, det4 = derive_entry_symbols(bj, ansi, "", raw, max_n=40, scratch=None)
        chk("反例", "不剥颜色码的退化实现确实会抽出残片，说明上一条不是空转",
            any(x in ("mFAILURES", "mintegration") for x in det4["error"]))
        chk("反例", "不给报错符号时排序退化为原顺序（说明这条排序确实由报错驱动）",
            EB._rank_by_symptom(["zzz", "maxBy"], [])[0] == "zzz")
    finally:
        shutil.rmtree(tmp, ignore_errors=True)
    print("mismatches: %d" % wrong)
    print("OUR_SLICE_SELFCHECK_%s" % ("OK" if wrong == 0 else "FAIL"))
    return 0 if wrong == 0 else 1


def main():
    if len(sys.argv) > 1 and sys.argv[1] == "--selfcheck":
        sys.exit(selfcheck())
    ap = argparse.ArgumentParser()
    ap.add_argument("--eco", choices=["npm", "pip"], default="npm")
    ap.add_argument("--lib", required=True)
    ap.add_argument("--old", required=True)
    ap.add_argument("--new", required=True)
    ap.add_argument("--src-dir", default="")
    ap.add_argument("--error-log", default="")
    ap.add_argument("--source-diff", default="")
    ap.add_argument("--test-diff", default="")
    ap.add_argument("--test-diff-note", default="")
    ap.add_argument("--behavior-json", default="")
    ap.add_argument("--out-dir", required=True)
    ap.add_argument("--scratch", default="", help="directory for intermediate products; defaults to out-dir when not given")
    ap.add_argument("--max-symbols", type=int, default=40)
    sys.exit(run(ap.parse_args()))


if __name__ == "__main__":
    main()

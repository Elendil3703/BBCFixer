#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
Library diff filtering (Section 3.2.2): extract the diffs of the upstream library, cross-check
them, and condense them into one structured "adaptation basis".

Hard rule:
    Never hand the whole raw code diff / test diff to the agent as evidence.
    Source diffs and test diffs are always sliced by "entry symbols" to the relevant subset first;
    when no symbol can be derived, only write "a pointer + the usage of the slicing tools" so that
    the agent slices by symbol on demand instead of reading thousands of lines.

Where the entry symbols come from (derived automatically, no need to wait for the agent):
    1) explicit --symbol (repeatable) has the highest priority;
    2) otherwise, "symbols changed" in the japicmp interface diff, intersected with "symbols
       actually used" in the downstream source (reachability slicing to the interfaces the client
       really touches);
       new symbols of the same kind (same japicmp change block) are included as well, which helps
       finding the new usage in the test diff.
    If neither works, fall back to "pointer + tools"; never dump the whole diff.

Symptom anchoring (--error-log passes in the real error output):
    the runner first runs one build at the pinned version and passes the distilled error log in;
    distinctive symbols extracted from the error lines are used to RANK and EXTEND the entry
    symbols: symbols that appear in the error come first (both the slices and the japicmp excerpt
    are centred on them), and symbols named by the error inside the japicmp changes that the
    downstream-identifier intersection missed are added. This follows the BUMP idea of
    "error line x API change cross-location of the root cause", extended to test-failure symptoms.

Second-hop tracing (lightweight automation, ALGO_HOPS=0 disables it):
    after the first slice of the source diff by entry symbols, extract "deeper changed symbols"
    from the changed lines inside the slice (a behavioural change is often not in the entry method
    itself but in what it calls internally), merge them into the slicing keys and slice once more.
    Deeper tracing is still done by the agent with the tools; a static call graph would be an
    optional heavyweight alternative, not implemented.

It chains the diff tools under tools/, writes the sliced evidence to evidence/<PROJ>/, and then
generates adaptation-basis.md, the scaffold of the condensed summary: the mechanically extractable
parts are filled in automatically, the parts that require understanding (behaviour change
description, cross-check conclusion) are left as a template for the agent to complete.

No direct LLM call: the last step of the summary is done by the agent with the injected preamble.

Usage:
  extract_basis.py --proj P --group G --art A --old O --new N \
                   [--compare URL] [--src-dir <downstream source root>] \
                   [--symbol S ...] [--max-symbols 40] [--symptom "<one-line symptom>"]
The output directory defaults to evidence/<PROJ>/ (override with --out-dir).
"""
import argparse, os, re, subprocess, sys

ROOT = os.path.dirname(os.path.abspath(__file__))
TOOLS = os.path.join(ROOT, "tools")

# Keyword / annotation / type noise in the japicmp output; excluded when deriving entry symbols
_STOP = {
    "PUBLIC", "PRIVATE", "PROTECTED", "STATIC", "FINAL", "ABSTRACT", "DEFAULT",
    "CLASS", "INTERFACE", "METHOD", "FIELD", "CONSTRUCTOR", "ENUM", "ANNOTATION",
    "NEW", "REMOVED", "MODIFIED", "UNCHANGED", "SUPERCLASS", "GENERIC",
    "TEMPLATES", "FORMAT", "VERSION", "FILE", "WARNING", "SERIALIZABLE",
    "not", "serializable", "Comparing", "against", "Hence", "Users",
    "Object", "String", "Comparable", "Override", "FunctionalInterface",
    "boolean", "int", "double", "void", "long", "float", "char", "byte", "short",
}
_IDENT = re.compile(r"[A-Za-z_]\w+")          # at least two characters; drops single-letter generics T/E/K/V
_METHOD = re.compile(r"([A-Za-z_]\w*)\s*\(")  # "name(" : a method name
# Runtime stack frame: "    at org.slf4j.LoggerFactory.getLogger(LoggerFactory.java:329)".
# The generic extraction (_line_syms -> _simple_names) keeps only the last segment of a fully
# qualified name; on a frame that is the method name (often too generic and dropped), and the
# distinctive simple CLASS name would be lost entirely, so frames are extracted explicitly.
_FRAME = re.compile(r"^\s*(?:\[ERROR\]\s*)?at\s+([\w.$]+)\.([\w$<>]+)\s*\(")
# Boilerplate annotations on japicmp lines; stripped before extracting symbols so that
# not/serializable/(+)/(-) do not leak in
_JNOISE = re.compile(r"\((?:not )?serializable\)|\([+\-]\)|PUBLIC|PRIVATE|PROTECTED")
# Identifiers that are too generic: they appear everywhere in a diff, so slicing by them is no
# slicing at all; excluded from the entry symbols.
# Only "bare generic names" are dropped; distinctive compound names such as query1nn / queryKnn /
# PointIndex are kept.
_GENERIC = {
    "iterator", "Iterator", "contains", "remove", "removeIf", "update", "insert",
    "next", "reset", "set", "get", "put", "add", "clear", "size", "isEmpty",
    "hasNext", "toString", "equals", "hashCode", "compare", "compareTo", "test",
    "apply", "main", "value", "point", "Point", "distance", "max", "min", "lower",
    "upper", "key", "name", "getName", "query", "index", "Index", "entry", "Entry",
    "node", "Node", "list", "List", "dist", "Stats", "Comparator", "Predicate",
    "Function", "extends", "values", "keys", "data", "result", "count",
    "matches", "append", "Iterable", "Deprecated", "Override", "equals",
    # Bare "factory / generic method names": without the class qualifier they are everywhere
    # (MinHeap.create / Foo.build ...), and slicing by them pulls in many unrelated files. The
    # relevant usages are reached through the distinctive OWNING CLASS name anyway, so drop them.
    "create", "build", "of", "valueOf", "newInstance", "from", "make", "parse",
    "format", "copy", "open", "close", "run", "start", "stop", "init", "load",
    "read", "write", "accept", "find", "all", "empty", "length",
}


# Java keywords and error-log boilerplate words: they appear massively in changed source lines and
# in Maven errors and have no discriminating power as slicing keys / anchor symbols
# (private/public/import/return...; cannot/symbol/method...), so all of them are dropped.
_NOISE = {
    "private", "public", "protected", "static", "return", "import", "package",
    "extends", "implements", "throws", "abstract", "final", "native", "volatile",
    "transient", "synchronized", "interface", "class", "enum", "instanceof",
    "default", "strictfp", "switch", "while", "catch", "finally", "throw",
    "assert", "continue", "break", "double", "boolean",
    "cannot", "symbol", "method", "location", "error", "ERROR", "FAILED",
    "WARNING", "Failed", "failure", "Compilation", "compile", "execute", "goal",
    "project", "expected", "required", "found", "Tests", "BUILD", "SUCCESS",
    "FAILURE", "surefire", "maven", "plugin", "plugins", "apache",
    # Test report boilerplate ("Tests run:" lines, "Caused by:" lines, "Time elapsed" timing):
    # they would take up the capped slots of symptom anchoring and push out the real
    # assertion values / root-cause symbols
    "Caused", "Errors", "Failures", "Skipped", "elapsed",
}


def _distinctive(s):
    """Is the symbol "distinctive" enough to slice by: not a generic name / keyword / error
    boilerplate word, and either a compound / camel-case / digit-bearing type name, or long enough."""
    if s in _GENERIC or s in _STOP or s in _NOISE or len(s) < 3:
        return False
    if re.search(r"[a-z][A-Z]|[A-Z].*[A-Z]|[0-9]", s):  # compound / camel-case / with digits (PointIndex, query1nn, KDTree)
        return True
    return len(s) >= 6                                    # otherwise it must be long enough (create=6 kept, set/next dropped)


def sh(script, *args, out=None):
    """Run a script under tools/, return (rc, stdout text). If out is given, also write it to that file."""
    cmd = [os.path.join(TOOLS, script)] + [str(a) for a in args]
    try:
        p = subprocess.run(cmd, capture_output=True, text=True,
                            encoding="utf-8", errors="replace", timeout=900)
        text = p.stdout + (("\n[stderr]\n" + p.stderr) if p.stderr.strip() else "")
    except Exception as e:
        return 1, f"(calling {script} failed: {e})"
    if out:
        with open(out, "w", encoding="utf-8", errors="replace") as f:
            f.write(text)
    return p.returncode, text


def head(text, n=60):
    lines = [l for l in text.splitlines() if l.strip()]
    return "\n".join(lines[:n])


def _simple_names(fqn):
    """org.tinspin.index.Index$PointEntryKnn<T> -> {Index, PointEntryKnn}"""
    fqn = re.sub(r"<[^>]*>", "", fqn)            # strip generics
    seg = fqn.split(".")[-1] if "." in fqn else fqn
    out = set()
    for part in seg.split("$"):
        for m in _IDENT.findall(part):
            if m not in _STOP:
                out.add(m)
    return out


def _line_syms(line):
    line = _JNOISE.sub(" ", line)
    toks = set()
    for fq in re.findall(r"[\w.$]+(?:<[^>]*>)?", line):
        toks |= _simple_names(fq)
    m = _METHOD.search(line)
    if m and m.group(1) not in _STOP:
        toks.add(m.group(1))
    return toks


def parse_japicmp_blocks(text):
    """Split the japicmp output into blocks per "changed class" (the prose before the first class
    declaration, headers / warnings, is ignored).
    Returns [ {cls: set of simple class names, changed: simple names of the symbols involved in the
    REMOVED/NEW/MODIFIED members of that block} ].
    Entry symbols are derived only from changed (the APIs that really changed), never from the
    prose, to avoid noise."""
    blocks, cur = [], None
    for line in text.splitlines():
        is_cls = (not line.startswith("\t")) and re.search(
            r"(MODIFIED|NEW|REMOVED)\s+(CLASS|INTERFACE)", line)
        if is_cls:
            if cur:
                blocks.append(cur)
            cur = {"cls": _line_syms(line), "changed": set()}
            continue
        if cur is None:           # no class declaration seen yet: prose header, discard
            continue
        if re.search(r"REMOVED|NEW|MODIFIED METHOD|MODIFIED FIELD|MODIFIED CONSTRUCTOR", line):
            cur["changed"] |= _line_syms(line)
    if cur:
        blocks.append(cur)
    return blocks


def collect_downstream_ids(src_dir):
    """Set of identifiers that occur in the downstream source (.java / .py); used to intersect the
    japicmp changes with the interfaces the client really uses."""
    ids = set()
    if not src_dir or not os.path.isdir(src_dir):
        return ids
    for dp, _, fns in os.walk(src_dir):
        if any(s in dp for s in (os.sep + "target", os.sep + ".git", os.sep + "build")):
            continue
        for fn in fns:
            if not fn.endswith((".java", ".py")):
                continue
            try:
                with open(os.path.join(dp, fn), encoding="utf-8", errors="ignore") as f:
                    for m in _IDENT.findall(f.read()):
                        ids.add(m)
            except Exception:
                pass
    return ids


def collect_downstream_dep_symbols(src_dir, group):
    """Set of simple names of the symbols the downstream project imports from the PACKAGE OF THE
    LIBRARY ITSELF ("upstream symbols actually called by the downstream project"). This is the
    mechanical basis for deriving entry symbols automatically for BEHAVIOURAL breakage: there
    japicmp reports no change (public signatures are unchanged), so "japicmp intersected with
    downstream" yields nothing, and we fall back to the class names the downstream project imports
    from the library package (e.g. zt-exec imports org.slf4j.{Logger,LoggerFactory,MDC}) and slice
    the source diff to the small part related to those symbols. Only imports are read, only the
    library's package prefix is taken: purely mechanical, no case knowledge."""
    out = set()
    if not src_dir or not os.path.isdir(src_dir) or not group:
        return out
    # Library package prefix: the Maven group is normally the Java package prefix
    # (org.slf4j / com.fasterxml.jackson.core ...)
    gpat = re.compile(r"^\s*import\s+(?:static\s+)?" + re.escape(group) + r"\.([\w.$]+)")
    for dp, _, fns in os.walk(src_dir):
        if any(s in dp for s in (os.sep + "target", os.sep + ".git", os.sep + "build")):
            continue
        for fn in fns:
            if not fn.endswith(".java"):
                continue
            try:
                with open(os.path.join(dp, fn), encoding="utf-8", errors="ignore") as f:
                    for line in f:
                        m = gpat.match(line)
                        if not m:
                            continue
                        # org.slf4j.Logger -> Logger; org.slf4j.spi.SLF4JServiceProvider -> SLF4JServiceProvider
                        tail = m.group(1).rstrip(";").strip()
                        simple = tail.split(".")[-1]
                        if simple and simple != "*":
                            out.add(simple)
            except Exception:
                pass
    return out


def parse_error_symbols(error_log):
    """Extract distinctive symbols from the (distilled) error log passed in by the runner, used as
    symptom anchors.
    Returns two lists, (error-message symbols, stack-frame symbols), each deduplicated in order of
    first occurrence.
    Message symbols: the earlier in the error, the closer to the first failing symbol.
    Frame symbols: the runner's distillation keeps the stack frames that belong to the upstream
    package; when the exception is thrown deep inside the library, the stack is a ready-made
    "real runtime call chain", and the class names in the frames bring root-cause symbols that the
    downstream project never calls directly into the slicing keys (making up for the absent static
    call graph). The top of a Java stack (the first frame) is closest to the throw point, so the
    first-occurrence order puts the deep symbols first. Assertion failures have no upstream frames,
    so that list is simply empty."""
    syms, frame_syms, seen = [], [], set()
    if not error_log or not os.path.isfile(error_log):
        return syms, frame_syms
    with open(error_log, encoding="utf-8", errors="ignore") as f:
        for line in f:
            m = _FRAME.match(line)
            if m:
                toks = set(_simple_names(m.group(1)))          # simple class names (inner classes split on $)
                mth = m.group(2)
                if "<" not in mth and not mth.startswith(("lambda$", "access$")):
                    toks.add(mth)                              # method name (constructors / synthetic methods dropped)
                for s in toks:
                    if s not in seen and _distinctive(s):
                        seen.add(s); frame_syms.append(s)
                continue
            for s in _line_syms(line):
                if s not in seen and _distinctive(s):
                    seen.add(s); syms.append(s)
    return syms, frame_syms


def _rank_by_symptom(symbols, err_syms):
    """Symptom-anchored ordering: symbols that appear in the error come first (stable sort, the
    original order is kept within each group)."""
    if not err_syms:
        return list(symbols)
    hit = set(err_syms)
    return [s for s in symbols if s in hit] + [s for s in symbols if s not in hit]


def python_modules_of(cache_dir):
    """Mechanically derive the IMPORT NAMES of the library from the unpacked new-src in the cache
    (the PyPI package name differs from the import name, e.g. PyYAML -> yaml).
    Takes the top-level directories containing __init__.py plus single-file modules in the root;
    supports the src/ layout. Pure directory-structure probing, no case knowledge."""
    mods = set()
    for base in ("new-src", "old-src"):
        root = os.path.join(cache_dir, base)
        if not os.path.isdir(root):
            continue
        roots = [root]
        if os.path.isdir(os.path.join(root, "src")):
            roots.append(os.path.join(root, "src"))
        for r in roots:
            for name in os.listdir(r):
                full = os.path.join(r, name)
                if os.path.isdir(full) and os.path.isfile(os.path.join(full, "__init__.py")):
                    mods.add(name)
                elif name.endswith(".py") and name not in ("setup.py", "conftest.py") \
                        and not name.startswith("test"):
                    mods.add(name[:-3])
        if mods:
            break
    return mods


def collect_downstream_dep_symbols_py(src_dir, modules):
    """Python version of "upstream symbols actually called by the downstream project": the A, B of
    `from <mod>[...] import A, B` in the downstream .py files, plus the module name itself (the
    attribute-style usage of `import <mod>` is sliced by the module name). Same semantics as the
    Java version."""
    out = set()
    if not src_dir or not os.path.isdir(src_dir) or not modules:
        return out
    mods_re = "|".join(re.escape(m) for m in modules)
    from_re = re.compile(r"^\s*from\s+(" + mods_re + r")(?:\.[\w.]+)?\s+import\s+(.+)$")
    imp_re = re.compile(r"^\s*import\s+(" + mods_re + r")\b")
    for dp, _, fns in os.walk(src_dir):
        if any(x in dp for x in (os.sep + ".git", os.sep + "__pycache__", os.sep + ".tox")):
            continue
        for fn in fns:
            if not fn.endswith(".py"):
                continue
            try:
                with open(os.path.join(dp, fn), encoding="utf-8", errors="ignore") as f:
                    for line in f:
                        m = from_re.match(line)
                        if m:
                            out.add(m.group(1))
                            for part in m.group(2).split(","):
                                name = part.strip().split(" as ")[0].strip().strip("()")
                                if name and name != "*":
                                    out.add(name)
                            continue
                        m = imp_re.match(line)
                        if m:
                            out.add(m.group(1))
            except Exception:
                pass
    return out


def derive_entry_symbols(if_text, src_dir, explicit, max_n, group="", err_syms=None,
                         eco="java", dep_modules=None):
    """Derive the entry symbols automatically. Returns (symbols_list, note).

    Two routes:
      - interface / rename breakage: japicmp changed symbols intersected with the symbols used by
        the downstream project (reachability slicing);
      - behavioural breakage (no japicmp change, or no intersection with the downstream project):
        fall back to "the symbols the downstream project imports from the library package"
        ("upstream symbols actually called by the downstream project") and slice the source diff
        by them; the diagnostic information hides only in the source diff, invisible to japicmp
        (the slf4j binding switching to ServiceLoader in zt-exec is of this kind).
    Both routes are first ordered by symptom anchoring (err_syms: symbols that appear in the error
    come first), implementing the data flow "symptom symbols -> slicing"."""
    err_syms = err_syms or []
    if explicit:
        return explicit[:max_n], f"given explicitly: {', '.join(explicit[:max_n])}"

    def _behavioral_fallback(reason):
        """Behavioural fallback: use the library symbols imported by the downstream project as entry
        symbols (symptom-anchored ordering)."""
        if eco == "python":
            raw = collect_downstream_dep_symbols_py(src_dir, dep_modules or set())
        else:
            raw = collect_downstream_dep_symbols(src_dir, group)
        dep_syms = sorted(s for s in raw if _distinctive(s))
        if not dep_syms:
            return [], reason + "; and the downstream project imports no recognisable symbol of this dependency; use --symbol or slice in place"
        dep_syms = _rank_by_symptom(dep_syms, err_syms)[:max_n]
        anchored = ", symbols named in the error ranked first" if err_syms else ""
        return dep_syms, (f"behavioral fallback ({reason}): the symbols of this dependency imported by the downstream project are used as entry symbols"
                          f" (the library symbols the downstream project actually calls{anchored}): {', '.join(dep_syms)}")

    blocks = parse_japicmp_blocks(if_text)
    if not blocks:
        return _behavioral_fallback("no japicmp change to slice by")
    down = collect_downstream_ids(src_dir)
    if not down:
        return [], "no downstream source given (--src-dir) or it has no identifiers, so no automatic intersection; use --symbol or slice in place"

    # Core entry symbols = symbols that are actually used by the downstream project, changed, and
    # distinctive enough (reachability slicing to the interfaces the client really touches).
    # No expansion to "all changed symbols of the related blocks": that would turn classes the
    # downstream project never uses (Box*/PHTree* etc.) into slicing keys and make the slice
    # meaningless. In rename breakage the old and new names are usually the -/+ lines of the same
    # hunk, so slicing by the old name already brings the usage of the new name along.
    broken, seen = [], set()
    for b in blocks:
        hit = {s for s in (b["changed"] & down) if _distinctive(s)}
        for s in sorted(hit):
            if s not in seen:
                seen.add(s); broken.append(s)
    all_changed = set().union(*(b["changed"] for b in blocks)) if blocks else set()
    # Symptom extension: symbols in the japicmp changes that the error names directly but that did
    # not make it into the "intersection with downstream identifiers" are added as entry symbols too
    # (being named by the error proves relevance; this is the mechanised form of the BUMP idea of
    # "error line x API change cross-location").
    for s in (err_syms or []):
        if s in all_changed and s not in seen and _distinctive(s):
            seen.add(s); broken.append(s)
    if not broken:
        return _behavioral_fallback("the japicmp changes do not intersect the downstream usage (mostly a behavioral break)")
    broken = _rank_by_symptom(broken, err_syms)
    # Add the new names that are "case variants" (query1NN -> query1nn, queryKNN -> queryKnn), to
    # cover the case where the old and new names are not in the same hunk
    low = {s.lower() for s in broken}
    variants = sorted(c for c in all_changed
                      if c.lower() in low and c not in seen and _distinctive(c))
    ordered = broken + [c for c in variants if not (c in seen or seen.add(c))]
    truncated = len(ordered) > max_n
    ordered = ordered[:max_n]
    anchored = ", symbols named in the error ranked first" if err_syms else ""
    note = f"derived automatically (japicmp changes ∩ downstream usage, including related class names and new symbols of the same block{anchored}): {', '.join(ordered)}"
    if truncated:
        note += f"  [capped at the first {max_n}; the full interface changes are in interface-diff.txt, slice further with the tools as needed]"
    return ordered, note


MAX_DIFF_LINES = 600   # hard cap applied after slicing: whatever the symbol choice, never hand thousands of diff lines to the agent

# License / copyright boilerplate: a block at the top of every source file, carrying no adaptation
# information but eating the slice budget and the agent's context; stripped line by line.
_LICENSE = re.compile(
    r"copyright|licensed under|the license\b|apache\.org/licenses|without warranties|"
    r"limitations under|you may (not )?(use|obtain)|obtain a copy|distributed under|"
    r"this file is part of|all rights reserved|spdx-license|on an .AS IS. basis|"
    r"either express or implied|specific language governing|"
    r"unless required by applicable|agreed to in writing|in compliance with|"
    r"warranties or conditions|redistribution|gnu (general|lesser)|mozilla public|"
    r"free software foundation|permission is hereby granted", re.I)


def _strip_boilerplate(text):
    """Remove license boilerplate lines and bare comment-marker lines from a diff text; keep the
    diff structure lines and javadoc that carries text."""
    out = []
    for ln in text.splitlines():
        if ln.startswith(("diff --git", "index ", "--- ", "+++ ", "@@", "#")):
            out.append(ln); continue              # diff structure lines / our own title lines: keep
        body = ln[1:] if ln[:1] in "+- " else ln  # strip the +/-/space prefix to look at the body
        s = body.strip()
        if _LICENSE.search(body):                 # license text: drop
            continue
        if s in ("/*", "*/", "*", "//"):          # bare comment-marker lines: drop (skeleton of the license block)
            continue
        out.append(ln)
    return "\n".join(out)


def _drop_reformat_pairs(text):
    """Drop "pure reformatting" pairs of removed/added lines: within one hunk, a '-' line and a '+'
    line with identical content after removing whitespace mean only indentation / line wrapping
    changed, with no semantic change, so the pair is removed.

    Motivation: slf4j 2.0 reformatted files such as NOPLogger.java wholesale; hundreds of
    "remove old, add new, content unchanged" pairs carry no adaptation information but eat the
    slice line budget and push the real root-cause section past the cap."""
    def norm(ln):
        return re.sub(r"\s+", "", ln[1:])

    out, hunk = [], None

    def flush():
        if hunk is None:
            return
        minus, plus = {}, {}
        for ln in hunk:
            if ln[:1] == "-" and not ln.startswith("---"):
                k = norm(ln); minus[k] = minus.get(k, 0) + 1
            elif ln[:1] == "+" and not ln.startswith("+++"):
                k = norm(ln); plus[k] = plus.get(k, 0) + 1
        budget = {k: min(minus[k], plus[k]) for k in minus if k in plus and k}
        used = {}
        for ln in hunk:
            sign = ln[:1]
            if sign in "+-" and not ln.startswith(("+++", "---")):
                k = norm(ln)
                if used.get((sign, k), 0) < budget.get(k, 0):
                    used[(sign, k)] = used.get((sign, k), 0) + 1
                    continue
            out.append(ln)

    for ln in text.splitlines():
        if ln.startswith("@@") or ln.startswith(("diff ", "index ", "--- ", "+++ ", "# ")):
            flush(); hunk = None
            out.append(ln)
            if ln.startswith("@@"):
                hunk = []
            continue
        if hunk is not None:
            hunk.append(ln)
        else:
            out.append(ln)
    flush()
    return "\n".join(out)


FOCUS_CTX = int(os.environ.get("ALGO_FOCUS_CTX", "3"))  # lines of context kept around each symbol-bearing changed line in the focused slice


def _focus_to_symbols(text, regex, ctx=FOCUS_CTX):
    """Tighten "slice by hunk" to "focus by line": keep only the removed/added lines that contain an
    entry symbol plus ctx lines of context, together with their file header (+++ line) and hunk
    header (@@ line) for orientation; upstream changes that contain no entry symbol at all are
    dropped wholesale.

    Motivation: slicing by hunk pulls in dozens of unrelated changed lines of a whole block because
    one line touches a symbol (in one measured case only 15 of 446 changed lines really contained an
    entry symbol). After focusing, only the few "old -> new migration" lines the downstream project
    cares about remain. An empty regex returns the text unchanged.
    If behavioural breakage needs more context, raise the environment variable ALGO_FOCUS_CTX."""
    if not regex:
        return text
    pat = re.compile(regex)
    lines = text.splitlines()
    n = len(lines)
    titles = [l for l in lines if l.startswith("# ")]
    file_hdr = [None] * n          # the "file header (+++ line)" each line belongs to
    hunk_hdr = [None] * n          # the "hunk header (@@ line)" each line belongs to
    cf = ch = None
    for i, ln in enumerate(lines):
        if ln.startswith("+++ "):
            cf = ln; ch = None
        elif ln.startswith("@@"):
            ch = ln
        file_hdr[i] = cf; hunk_hdr[i] = ch
    keep = [False] * n
    for i, ln in enumerate(lines):
        if ln[:1] in "+-" and not ln.startswith(("+++", "---")) and pat.search(ln[1:]):
            for j in range(max(0, i - ctx), min(n, i + ctx + 1)):
                lj = lines[j]
                if not lj.startswith(("diff ", "index ", "--- ", "+++ ", "@@", "# ")):
                    keep[j] = True
    out = list(titles)
    last_f = last_h = None
    prev = -2
    for i in range(n):
        if not keep[i]:
            continue
        if file_hdr[i] != last_f:
            out.append("")
            if file_hdr[i]:
                out.append(file_hdr[i])
            last_f, last_h, prev = file_hdr[i], None, -2
        if hunk_hdr[i] != last_h:
            if hunk_hdr[i]:
                out.append(hunk_hdr[i])
            last_h, prev = hunk_hdr[i], -2
        if prev >= 0 and i - prev > 1:
            out.append("    …")        # unrelated lines skipped within the same hunk
        out.append(lines[i]); prev = i
    return "\n".join(out) if len(out) > len(titles) else text


def _rank_sections_by_relevance(text, err_regex):
    """Before capping, reorder the per-file sections by SYMPTOM RELEVANCE: sections hit by the error
    symbols (err_regex) come first.

    Motivation: the slice is emitted in the file order of the diff, so a large but secondary change
    such as Logger.java (2.0 added a whole fluent API, and symbols like DEBUG/INFO hit everywhere)
    sorts alphabetically first, burns the line cap, and the real root-cause section
    (LoggerFactory.java switching the binding to ServiceLoader and falling back to NOP) is cut off
    entirely. The line cap must "keep by relevance", not "truncate by file order".
    Score = number of error-symbol hits (symptom anchoring first)."""
    if not err_regex:
        return text
    pat = re.compile(err_regex)
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
            titles.append(ln)          # stray lines before the first file header go to the title part
    if cur:
        sections.append(cur)
    if len(sections) <= 1:
        return text
    scored = [(sum(1 for l in sec if pat.search(l)), i, sec) for i, sec in enumerate(sections)]
    scored.sort(key=lambda x: (-x[0], x[1]))   # more hits first; ties keep the original order
    out = titles[:]
    for _, _, sec in scored:
        out.extend(sec)
    return "\n".join(out)


def slice_to(script, group, art, old, new, regex, out_path, err_regex=""):
    """Slice the union of the symbols, strip license boilerplate, focus by line around the symbols,
    reorder the sections by symptom relevance, then apply the hard cap.
    Writes out_path and returns the final text."""
    _, t = sh(script, group, art, old, new, "--symbol", regex)
    t = _strip_boilerplate(t)
    t = _drop_reformat_pairs(t)
    t = _focus_to_symbols(t, regex)
    t = _rank_sections_by_relevance(t, err_regex)
    lines = t.splitlines()
    if len(lines) > MAX_DIFF_LINES:
        t = "\n".join(lines[:MAX_DIFF_LINES]) + (
            f"\n\n... [capped at {MAX_DIFF_LINES} lines; the slice is still large (sections are ordered by symptom relevance, the least relevant ones were cut). "
            f"Slice further in place with more precise entry symbols: tools/{script} <G> <A> <O> <N> --symbol '<narrower symbol>'] ...\n")
    with open(out_path, "w", encoding="utf-8", errors="replace") as f:
        f.write(t)
    return t


HOP_MAX = int(os.environ.get("ALGO_HOP_MAX", "8"))   # maximum number of symbols added by second-hop tracing


def hop_symbols(sliced_text, current):
    """Second-hop tracing (lightweight automation): from the CHANGED LINES of the first slice,
    extract "which deeper changed symbols the entry method calls internally" and use them as the
    slicing keys of the second hop.

    Only distinctive identifiers on changed lines (-/+) are taken: a behavioural change is often
    not in the entry method itself but deeper in what it calls (in zt-exec, getLogger -> provider
    lookup -> NOP fallback are several hops apart); those deep symbols appear on the changed lines
    of the entry-symbol slice but are not entry symbols themselves. The top HOP_MAX by frequency
    are kept. Deeper tracing is still done by the agent with the tools; a static call graph would
    be an optional heavyweight alternative, not implemented."""
    cur = set(current)
    freq = {}
    for ln in sliced_text.splitlines():
        if ln[:1] not in "+-" or ln.startswith(("+++", "---")):
            continue
        for s in _line_syms(ln):
            if s in cur or not _distinctive(s):
                continue
            freq[s] = freq.get(s, 0) + 1
    # At least two occurrences to count as a "repeatedly touched deep symbol"; suppresses one-off noise
    cands = [s for s, n in sorted(freq.items(), key=lambda kv: -kv[1]) if n >= 2]
    return cands[:HOP_MAX]


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--proj", required=True)
    ap.add_argument("--group", required=True)
    ap.add_argument("--art", required=True)
    ap.add_argument("--old", required=True)
    ap.add_argument("--new", required=True)
    ap.add_argument("--compare", default="")
    ap.add_argument("--eco", choices=["java", "python"], default="java",
                    help="ecosystem of the library: java=Maven Central+japicmp; python=PyPI+ast signature comparison")
    ap.add_argument("--src-dir", default="", help="downstream source root, used to derive entry symbols automatically (reachability slicing to the interfaces the client actually uses)")
    ap.add_argument("--symbol", action="append", default=[], help="entry symbol, repeatable; when given, no automatic inference")
    ap.add_argument("--max-symbols", type=int, default=40)
    ap.add_argument("--symptom", default="")
    ap.add_argument("--error-log", default="", help="distilled error log of the runner's first build at the pinned version; used for symptom anchoring (error symbols are sliced first)")
    ap.add_argument("--out-dir", default="")
    a = ap.parse_args()

    ev_root = os.environ.get("ALGO_EVIDENCE", os.path.join(ROOT, "evidence"))
    out_dir = a.out_dir or os.path.join(ev_root, a.proj)
    os.makedirs(out_dir, exist_ok=True)

    print(f">>> [extract_basis] {a.art} {a.old} -> {a.new}  evidence dir: {out_dir}")

    # 0) Fetch the library (idempotent, skipped when cached; both ecosystems use the same cache
    #    layout, so code_diff_source/test_diff are shared)
    if a.eco == "python":
        sh("fetch_upstream_py.sh", a.art, a.old, a.new, a.compare)
    else:
        sh("fetch_upstream.sh", a.group, a.art, a.old, a.new, a.compare)
    cache_dir = os.path.join(os.environ.get("ALGO_CACHE", os.path.join(ROOT, "cache")),
                             f"{a.art}-{a.old}-{a.new}")
    has_repo = os.path.isfile(os.path.join(cache_dir, "repo-meta.txt"))

    # 1) Interface shape diff (japicmp). This is the "answer material" for interface-shape breakage;
    #    it is already a condensed --only-modified report, not a raw line-level diff, so writing it
    #    in full is acceptable; adaptation-basis only gets the excerpt related to the entry symbols.
    p_if = os.path.join(out_dir, "interface-diff.txt")
    # japicmp is slow; with a non-empty cached file and ALGO_REUSE_IF=1 it is reused instead of
    # rerun (the japicmp output does not depend on the downstream project, so it can be reused)
    reuse = os.environ.get("ALGO_REUSE_IF") and os.path.exists(p_if) \
        and sum(1 for _ in open(p_if, encoding="utf-8", errors="ignore")) > 5
    if reuse:
        print(">>> [extract_basis] reusing the cached interface-diff.txt (ALGO_REUSE_IF=1)")
    else:
        if a.eco == "python":
            sh("code_diff_interface_py.sh", a.art, a.old, a.new, p_if)
        else:
            sh("code_diff_interface.sh", a.group, a.art, a.old, a.new, p_if)
    if_text = open(p_if, encoding="utf-8", errors="ignore").read() if os.path.exists(p_if) else ""

    # 1b) Transitive-dependency / version-range evidence: diff of the dependency declarations of the
    #     library itself (its POM / requires_dist)
    p_dep = os.path.join(out_dir, "dependency-diff.txt")
    if a.eco == "python":
        sh("dep_diff_py.sh", a.art, a.old, a.new, p_dep)
    else:
        sh("dep_diff.sh", a.group, a.art, a.old, a.new, p_dep)
    dep_text = open(p_dep, encoding="utf-8", errors="ignore").read() if os.path.exists(p_dep) else ""
    dep_changed = [l for l in dep_text.splitlines()
                   if l[:1] in "+-" and not l.startswith(("+++", "---"))]
    dep_excerpt = "\n".join(dep_changed[:40]) or "(no difference in the dependency declarations)"

    # 1c) Migration-guide diff (second tier, upstream natural language: CHANGELOG/upgrading etc.
    #     harvested by fetch_upstream_py; usually absent on the Java side)
    p_guide = os.path.join(out_dir, "guide-diff.txt")
    og, ng = os.path.join(cache_dir, "old-guide"), os.path.join(cache_dir, "new-guide")
    guide_excerpt = ""
    # BBCFixer only trusts code and execution evidence (code diff / test diff / measured behaviour
    # differences); migration guides (natural language such as CHANGELOG) are not part of the
    # evidence by default and are generated only with EVIDENCE_GUIDE=1 (not used in this package).
    guide_on = os.environ.get("EVIDENCE_GUIDE", "0") == "1"
    if not guide_on:
        try:
            if os.path.exists(p_guide): os.remove(p_guide)
        except OSError:
            pass
    elif os.path.isdir(og) or os.path.isdir(ng):
        gp = subprocess.run(["diff", "-ruN", og, ng], capture_output=True, text=True,
                            encoding="utf-8", errors="replace")
        # Drop the bare underline lines of rst/markdown and empty changed lines
        glines = [l for l in gp.stdout.splitlines()
                  if not re.fullmatch(r"[+\-][=\-~^+\s]*", l)]
        gtext = "\n".join(glines)
        with open(p_guide, "w", encoding="utf-8", errors="replace") as f:
            f.write("# migration notes diff (tier 2, library natural language)\n" + gtext[:120000])
        # Excerpt: keep only added lines (what the new version says), capped at 60 lines
        added = [l for l in glines if l.startswith("+") and not l.startswith("+++")][:60]
        guide_excerpt = "\n".join(added)
    else:
        with open(p_guide, "w", encoding="utf-8", errors="replace") as f:
            f.write("(no migration notes or CHANGELOG found in the library release)\n")

    # 2) Derive the entry symbols automatically (explicit > japicmp intersected with downstream usage
    #    > fall back to a pointer; error symbols first = symptom anchoring)
    msg_syms, frame_syms = parse_error_symbols(a.error_log)
    # Merged list for the symptom-anchored ordering of the entry symbols: message symbols first
    # (original behaviour preserved), frame symbols after them
    err_syms = msg_syms + [s for s in frame_syms if s not in msg_syms]
    if msg_syms:
        print(f">>> [extract_basis] symptom anchor symbols (from --error-log): {', '.join(msg_syms[:15])}")
    if frame_syms:
        print(f">>> [extract_basis] upstream stack-frame symbols (runtime call chain, deepest first): {', '.join(frame_syms[:10])}")
    dep_modules = python_modules_of(cache_dir) if a.eco == "python" else None
    if dep_modules:
        print(f">>> [extract_basis] library import names (mechanically probed): {', '.join(sorted(dep_modules))}")
    symbols, sym_note = derive_entry_symbols(if_text, a.src_dir, a.symbol, a.max_symbols,
                                             a.group, err_syms, a.eco, dep_modules)
    print(f">>> [extract_basis] entry symbols: {sym_note}")
    # Error symbols serve both as slicing keys and as relevance weights: being named by the error
    # proves relevance (e.g. NOP in the slf4j case: not in the japicmp changes, yet exactly the
    # keyword of the root-cause section, the LoggerFactory fallback logic); when capping, sections
    # containing error symbols are kept first.
    # Message symbols keep the original cap of 10; frame symbols get up to 6 extra slots of their
    # own so they do not compete with the message symbols (the frame class names are the only keys
    # that point at the root cause when "the entry symbol is unchanged and the real change is
    # deeper"; if they were pushed out, everything before would be wasted).
    err_slice = [s for s in msg_syms if _distinctive(s)][:10]
    err_slice += [s for s in frame_syms if s not in err_slice][:6]
    err_regex = "|".join(re.escape(s) for s in err_slice)
    slice_keys = symbols + [s for s in err_slice if s not in symbols]
    regex = "|".join(re.escape(s) for s in slice_keys) if slice_keys else ""

    # 3) Source diff: only the slice is written, never the whole dump; after the first slice one
    #    round of second-hop tracing (ALGO_HOPS=0 disables it)
    p_src = os.path.join(out_dir, "source-diff.txt")
    hops = []
    if slice_keys:
        sliced = slice_to("code_diff_source.sh", a.group, a.art, a.old, a.new, regex, p_src,
                          err_regex=err_regex)
        if os.environ.get("ALGO_HOPS", "1") != "0":
            hops = hop_symbols(sliced, slice_keys)
        if hops:
            print(f">>> [extract_basis] symbols added by second-hop tracing: {', '.join(hops)}")
            regex = "|".join(re.escape(s) for s in slice_keys + hops)
            slice_to("code_diff_source.sh", a.group, a.art, a.old, a.new, regex, p_src,
                     err_regex=err_regex)
            src_note = (f"sliced by entry symbols: {', '.join(symbols)}; "
                        f"added by second-hop tracing: {', '.join(hops)}")
        else:
            src_note = f"sliced by entry symbols: {', '.join(symbols)}"
    else:
        with open(p_src, "w", encoding="utf-8", errors="replace") as f:
            f.write("(No entry symbols could be derived automatically, so the whole source diff was not written, to avoid handing thousands of raw diff lines to the agent.\n"
                    "After determining the entry symbols in the second stage, slice in place with:\n"
                    "  tools/code_diff_source.sh <G> <A> <O> <N> --symbol <entry symbol>\n"
                    "or several at once: --symbol 'sym1|sym2|sym3')\n")
        src_note = "not sliced (slice in place with the tools once the entry symbols are known; do not read the whole diff)"

    # 4) Test diff: likewise only the slice is written (also ordered by symptom relevance, then capped)
    p_test = os.path.join(out_dir, "test-diff.txt")
    if (a.compare or has_repo) and slice_keys:
        slice_to("test_diff.sh", a.group, a.art, a.old, a.new, regex, p_test,
                 err_regex=err_regex)
        test_note = f"sliced by entry symbols: {', '.join(symbols)}"
    elif (a.compare or has_repo) and not slice_keys:
        with open(p_test, "w", encoding="utf-8", errors="replace") as f:
            f.write("(There is an upstream repository but no entry symbols were derived, so the whole test diff was not written, to avoid handing thousands of raw diff lines to the agent.\n"
                    "After determining the entry symbols, slice in place:\n"
                    "  tools/test_diff.sh <G> <A> <O> <N> --symbol '<sym1>|<sym2>')\n")
        test_note = "not sliced (slice in place with the tools once the entry symbols are known; do not read the whole diff)"
    else:
        with open(p_test, "w", encoding="utf-8", errors="replace") as f:
            f.write("(no compare URL and no upstream repository clone, so the upstream tags cannot be located; the test diff is unavailable)\n")
        test_note = "unavailable (no compare URL or upstream repository)"

    # Evidence guide (mechanically generated, an extension of symptom anchoring): count which
    # evidence sections each error symbol hits, steering the agent's attention straight to the
    # root-cause section (e.g. "NOP" hitting the LoggerFactory.java section in the slf4j case).
    # Pure grep counting, no case knowledge.
    def _section_hits(path, syms):
        hits = {}
        if not os.path.exists(path):
            return hits
        cur = None
        for ln in open(path, encoding="utf-8", errors="ignore"):
            if ln.startswith("# "):          # the slice title line itself contains all symbols; not counted
                continue
            if ln.startswith("+++ "):
                cur = os.path.basename(ln.split("\t")[0][4:].strip())
            if cur is None:
                continue
            for sym in syms:
                if sym in ln:
                    hits.setdefault(sym, {}).setdefault(cur, 0)
                    hits[sym][cur] += 1
        return hits

    guide_lines = []
    for fname, label in ((p_src, "source-diff.txt"), (p_test, "test-diff.txt")):
        for sym, per in _section_hits(fname, err_slice).items():
            tops = sorted(per.items(), key=lambda kv: -kv[1])[:3]
            frag = ", ".join(f"{sec} ×{n}" for sec, n in tops)
            guide_lines.append(f"- error symbol `{sym}` matches {label}: {frag}")
    evidence_guide = "\n".join(guide_lines) or "(the error symbols match no evidence fragment)"
    if guide_on:
        guide_section = ("## 2c. Library migration notes diff (tier 2, natural language; full text in guide-diff.txt. Only an aid to diagnosis and locating; confirm it against the tier-1 artifacts before relying on it)\n"
                         "```\n" + (guide_excerpt or "(no migration notes to collect)") + "\n```\n\n")
        guide_listed = " / guide-diff.txt"
    else:
        guide_section = ""
        guide_listed = ""

    # japicmp excerpt: only lines containing an entry symbol, capped at 60 lines; without symbols fall back to the head
    if symbols:
        rx = re.compile("|".join(re.escape(s) for s in symbols))
        if_excerpt = "\n".join([l for l in if_text.splitlines() if rx.search(l)][:60]) or head(if_text, 40)
    else:
        if_excerpt = head(if_text, 40)

    # 5) Adaptation-basis template (scaffold of the condensed summary; the text written below stays as-is,
    #    it is what the agent reads)
    basis = os.path.join(out_dir, "adaptation-basis.md")
    with open(basis, "w", encoding="utf-8", errors="replace") as f:
        f.write(f"""# Adaptation basis: {a.art} {a.old} -> {a.new} ({a.proj})

> This file is a scaffold for summarising the evidence. The parts that can be extracted mechanically are filled in;
> fill in the parts marked [to be filled in by the agent] after reading the evidence in the third stage, then adapt the code in the fourth stage.
> Note: the source and test diffs are **sliced** by entry symbols; do not read a whole raw diff; to see more, slice further by symbol with the tools.

## 0. Symptoms (from symptom anchoring in the first stage)
{a.symptom or '[to be filled in by the agent: first failing symbol / failing test and assertion / compile time or run time]'}

## 1. Entry symbols (the slicing keys)
{sym_note}

## 1b. Evidence guide (generated mechanically: which evidence fragments the symbols and actual values of the error match; read the most-matched fragments first)
{evidence_guide}

## 2. Interface diff (tier 1, artifact; japicmp, only the excerpt related to the entry symbols, full report in interface-diff.txt)
> Reading note: japicmp gives two **unpaired** lists, what was removed from a class (REMOVED) and what was added (NEW);
> it does **not** draw an arrow saying "old X corresponds to new Y". A pure rename (same parameters, only case or prefix/suffix differ) can be paired by name;
> but for any **structural signature change** (parameter count or types changed, moved into another nested class, return type changed, e.g. create(int,fn) -> create(IndexConfig)),
> japicmp cannot tell the old-to-new correspondence or how to fill in the new parameters; **go to section 4 and pair them using the real migration of the same call in the test diff**.
```
{if_excerpt or '(japicmp produced no output or was not run; possibly a purely behavioral break with an unchanged interface; look at sections 3 and 4)'}
```

## 2b. Transitive dependencies and version ranges (tier 1, artifact; dependency declaration diff of the upgraded library, full diff in dependency-diff.txt)
> When the compiler reports a missing class but japicmp shows no corresponding removal, look here first: a transitive dependency no longer declared by the new version ('-' line) is the root cause;
> changes of version ranges (such as an open range [2.0,)) also show here. For the full dependency tree, run mvn dependency:tree in the workspace.
```
{dep_excerpt}
```

{guide_section}## 3. Behavior mechanism (tier 1, artifact; the source diff is sliced, see source-diff.txt)
- Source diff: {src_note}
- Behavior change [to be filled in by the agent] (optional for an interface-shape break, required for a behavioral break):
  - Entry symbol:
  - Old behavior:
  - New behavior and mechanism:
  - Evidence (which fragments of source-diff.txt):
  - Confidence (confirmed by the test diff in the next section?):

## 4. Test diff (tier 1, artifact, sliced; see test-diff.txt)
- Status: {test_note}
- Old-to-new call pairing and new usage examples [optional for a pure rename; **required** whenever the signature changes structurally (parameters, types, nesting, return value), including interface-shape breaks]:
  in test-diff.txt find the fragment where the same call moves from the old form to the new form (paired - and + lines) and confirm from it:
  - which added symbol replaces the old symbol removed in section 2:
  - how the new interface is constructed and which arguments it takes (where the old arguments go in the new form, e.g. how (dimension, distance function) becomes an IndexConfig):
- Correct expected value of the new behavior [optional for an interface-shape break, required for a behavioral break]:

## 5. Cross-check and conclusion
- Do the "new behavior hypothesis" of section 3 and the "new expected value" of section 4 agree? [to be filled in by the agent: agree -> accept; disagree -> reread the source diff]:
- One-sentence adaptation basis [to be filled in by the agent: what changed, what the new correct usage or expected value is, how the downstream project should change]:

---
Raw evidence files (all sliced or condensed reports; do not read them whole): interface-diff.txt / source-diff.txt / test-diff.txt / dependency-diff.txt{guide_listed}
Slice further in place: tools/code_diff_source.sh / tools/test_diff.sh <G> <A> <O> <N> --symbol '<sym1>|<sym2>'
Dig into one symbol: tools/locate_behavior.sh <G> <A> <O> <N> <entry symbol>
Dependency declaration diff: tools/dep_diff.sh <G> <A> <O> <N>
""")
    print(f">>> evidence generated: {basis}")
    print("    - interface-diff.txt (condensed japicmp report) / source-diff.txt (sliced)"
          "/ test-diff.txt (sliced) / dependency-diff.txt (dependency declaration diff)")
    return 0


if __name__ == "__main__":
    sys.exit(main())

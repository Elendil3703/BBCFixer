#!/usr/bin/env python3
"""Merge the per-process records of js_boundary_trace.js into the trace.jsonl that behavior_diff.py understands.

A single `npm test` often starts several node processes (the npm wrapper, build scripts, the one that
actually runs the tests), and each process writes its own proc-<ordinal>-<pid>.jsonl. Merge rules:

  1. Keep only the processes that actually hooked something (hooked_pkgs non-empty). A process such as
     the npm wrapper that never requires the library produces no records; keeping it only makes the
     process count vary randomly between two runs.
  2. Concatenate in process ordinal order and renumber i, so the output contains no pid and nothing
     that varies between runs.
  3. Write the attached field explicitly in _meta. This is the core signal of this component: "no
     behavior difference" and "the recorder never attached" look identical in the output; when
     attached=false the caller must report an error and must never treat it as "the two versions
     behave identically".

Usage:
    js_boundary_merge.py --dir <per-process record directory> --out <capture-dir>/trace.jsonl
                         [--strict]   # exit with code 3 when attached=false
"""
import argparse
import glob
import json
import os
import sys


# The merge script may be run inside a container whose locale is C (the node:8 / node:10 images are such);
# there python3's standard output defaults to ASCII encoding and the Chinese messages in this file would crash it.
# reconfigure needs Python 3.7; the node:8 image only has 3.5, so fall back to wrapping in our own TextIOWrapper.
try:
    sys.stdout.reconfigure(encoding="utf-8", errors="replace")
    sys.stderr.reconfigure(encoding="utf-8", errors="replace")
except Exception:
    try:
        import io as _io
        sys.stdout = _io.TextIOWrapper(sys.stdout.buffer, encoding="utf-8", errors="replace", line_buffering=True)
        sys.stderr = _io.TextIOWrapper(sys.stderr.buffer, encoding="utf-8", errors="replace", line_buffering=True)
    except Exception:
        pass


def read_proc(p):
    head, recs = None, []
    with open(p, encoding="utf-8", errors="replace") as f:
        for line in f:
            line = line.strip()
            if not line:
                continue
            try:
                r = json.loads(line)
            except Exception:
                continue
            if r.get("_proc"):
                head = r
            else:
                recs.append(r)
    return head, recs


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--dir", required=True)
    ap.add_argument("--out", required=True)
    ap.add_argument("--keep", choices=["hooked", "all"], default="hooked")
    ap.add_argument("--strict", action="store_true")
    ns = ap.parse_args()

    procs = []
    for p in sorted(glob.glob(os.path.join(ns.dir, "proc-*.jsonl"))):
        head, recs = read_proc(p)
        if head is None:
            continue
        procs.append((head.get("ordinal", 0), os.path.basename(p), head, recs))
    procs.sort(key=lambda t: (t[0], t[1]))

    installed = sorted(glob.glob(os.path.join(ns.dir, "installed-*.txt")))
    dumperr = sorted(glob.glob(os.path.join(ns.dir, "dumperror-*.txt")))

    kept = [t for t in procs if (ns.keep == "all" or t[2].get("hooked_pkgs"))]

    out_recs = []
    pkgs, hooked, missing, integrity = [], [], [], []
    truncated = False
    esm_seen = False
    norm = {}
    reqs = {"total": 0, "internal": 0, "intermediate": 0, "hooked": 0}
    node_versions = []
    for _, _, head, recs in kept:
        for r in recs:
            r = dict(r)
            r["i"] = len(out_recs)
            out_recs.append(r)
        for k in head.get("pkgs") or []:
            if k not in pkgs:
                pkgs.append(k)
        for k in head.get("hooked_pkgs") or []:
            if k not in hooked:
                hooked.append(k)
        for k in head.get("missing_pkgs") or []:
            if k not in missing:
                missing.append(k)
        integrity.extend(head.get("integrity") or [])
        truncated = truncated or bool(head.get("truncated"))
        esm_seen = esm_seen or bool(head.get("esm_seen"))
        for k, v in (head.get("normalizations") or {}).items():
            norm[k] = norm.get(k, 0) + v
        for k, v in (head.get("requires") or {}).items():
            reqs[k] = reqs.get(k, 0) + v
        nv = head.get("node")
        if nv and nv not in node_versions:
            node_versions.append(nv)

    attached = bool(hooked)
    meta = {
        "_meta": True,
        "eco": "javascript",
        "pkgs": pkgs or [],
        "recorded": len(out_recs),
        "truncated": truncated,
        "attached": attached,
        "hooked_pkgs": sorted(hooked),
        "missing_pkgs": sorted(missing),
        "processes_installed": len(installed),
        "processes_dumped": len(procs),
        "processes_hooked": len(kept),
        "dump_errors": len(dumperr),
        "requires": reqs,
        "esm_seen": esm_seen,
        "normalizations": norm,
        # The probe mode of behavior_diff.py reads this field; the JavaScript-side equivalent evidence is
        # "whether the own properties of the library's export object still hold the same value at process
        # exit"; replaced means monkey patched.
        "monkeypatched": [
            {"module": h.get("module"), "attr": h.get("attr"),
             "defined_in": h.get("how") + ("=" + str(h.get("now")) if h.get("now") else "")}
            for h in integrity
        ],
        "node_versions": node_versions,
    }
    if not attached:
        meta["error"] = ("The boundary-call recorder attached to no library package: "
                         "installed=%d dumped=%d hooked=0 missing=%s. "
                         "an empty output here does not mean that the two versions behave the same; the caller must treat it as a failure."
                         % (len(installed), len(procs), missing))

    os.makedirs(os.path.dirname(os.path.abspath(ns.out)) or ".", exist_ok=True)
    with open(ns.out, "w", encoding="utf-8") as f:
        for r in out_recs:
            f.write(json.dumps(r, ensure_ascii=False, sort_keys=True) + "\n")
        f.write(json.dumps(meta, ensure_ascii=False, sort_keys=True) + "\n")

    print("js_boundary_merge: %s records, processes installed=%d dumped=%d hooked=%d, attached=%s"
          % (len(out_recs), len(installed), len(procs), len(kept), attached))
    if not attached:
        sys.stderr.write("js_boundary_merge: warning: " + meta["error"] + "\n")
        if ns.strict:
            sys.exit(3)


if __name__ == "__main__":
    main()

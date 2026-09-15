#!/usr/bin/env python3
"""Boundary call recorder (the evidence-collection component of differential execution, Section 3.2.1; Python ecosystem).

Purpose: while the tests run, record every boundary call from the downstream code into the upgraded
library (the upstream package): function name, arguments and return value (or exception), written as
JSONL. One record is taken in each of the v_old and v_new environments and handed to behavior_diff.py
for mechanical comparison, which locates the behavior difference at "the first call whose return value
differs".

Usage (as a pytest plugin, injected by diffexec_*.sh; no manual invocation needed):
    PYTHONPATH=<this directory> PYTEST_ADDOPTS="-p py_boundary_trace" \
    ALGO_TRACE_PKGS=marshmallow,netaddr ALGO_TRACE_OUT=/algo-out/trace.jsonl \
    python -m pytest .

Environment variables:
    ALGO_TRACE_PKGS   comma-separated top-level import names of the library packages (required; if empty the plugin silently does nothing)
    ALGO_TRACE_OUT    output JSONL path (default ./.algo-trace.jsonl)
    ALGO_TRACE_MAX    maximum number of boundary calls recorded (default 4000; beyond that recording stops and truncated is set)
    ALGO_TRACE_REPR   truncation length of the argument/return value repr (default 160)

What is recorded:
    Only "boundary calls": the callee belongs to the library package and the caller does not belong to
    the same package (i.e. the first hop from downstream or test code into the library). Calls inside the
    library package are not recorded (noise control; deeper mechanisms are covered by the source diff
    evidence). C extension functions have no Python frame and cannot be recorded via this path (marked
    honestly as a blind spot).

Implementation: sys.setprofile (only call/return events, lighter than settrace). Multi-threaded tests only
cover the main thread (threading.setprofile is also installed, best effort). No internal exception of the
recorder may affect the tests themselves.
"""
import json
import os
import sys
import threading

_PKGS = [p.strip() for p in os.environ.get("ALGO_TRACE_PKGS", "").split(",") if p.strip()]
_OUT = os.environ.get("ALGO_TRACE_OUT", ".algo-trace.jsonl")
_MAX = int(os.environ.get("ALGO_TRACE_MAX", "4000"))
_REPR = int(os.environ.get("ALGO_TRACE_REPR", "160"))

_records = []
_seq = [0]
_truncated = [False]
_code_flag = {}       # code object -> whether it belongs to the library package (cache, keeps the profile callback fast)
_pkg_dirs = []        # file path prefixes of the library packages


def _resolve_pkg_dirs():
    """Locate the installed directory prefix of each library package; if the import fails, fall back to path substring matching."""
    for pkg in _PKGS:
        try:
            mod = __import__(pkg)
            f = getattr(mod, "__file__", None)
            if f:
                d = os.path.dirname(os.path.abspath(f))
                if os.path.basename(f).startswith("__init__."):
                    _pkg_dirs.append(d + os.sep)          # package directory
                else:
                    _pkg_dirs.append(os.path.abspath(f))  # single-file module
                continue
        except Exception:
            pass
        # When that fails, match by path substring (site-packages/<pkg>/)
        _pkg_dirs.append(os.sep + pkg + os.sep)
        _pkg_dirs.append(os.sep + pkg + ".py")


def _in_pkg(code):
    flag = _code_flag.get(code)
    if flag is None:
        fn = code.co_filename or ""
        # absolute path prefixes use startswith; "/pkg/" style substring patterns (the fallback when the import failed) use in
        flag = False
        for d in _pkg_dirs:
            if os.path.isabs(d):
                if fn.startswith(d):
                    flag = True
                    break
            elif d in fn:
                flag = True
                break
        _code_flag[code] = flag
    return flag


def _safe_repr(v):
    try:
        s = repr(v)
    except Exception as e:  # repr itself may raise
        s = "<repr failed: %s>" % type(e).__name__
    if len(s) > _REPR:
        s = s[:_REPR] + "...(truncated)"
    return s


def _qualname(code):
    return getattr(code, "co_qualname", code.co_name)


class _Profiler:
    """Maintains a call stack per thread and only records on the call/return of boundary calls."""

    def __init__(self):
        self.local = threading.local()

    def _stack(self):
        st = getattr(self.local, "stack", None)
        if st is None:
            st = []
            self.local.stack = st
        return st

    def __call__(self, frame, event, arg):
        try:
            if _truncated[0]:
                return
            if event == "call":
                code = frame.f_code
                rec = None
                if _in_pkg(code):
                    back = frame.f_back
                    if back is None or not _in_pkg(back.f_code):
                        # boundary call: downstream/test code -> the library
                        args = {}
                        try:
                            n = code.co_argcount
                            names = code.co_varnames[:n]
                            for name in names:
                                if name == "self" or name == "cls":
                                    continue
                                if name in frame.f_locals:
                                    args[name] = _safe_repr(frame.f_locals[name])
                        except Exception:
                            pass
                        caller = ""
                        if back is not None:
                            caller = "%s:%d" % (
                                os.path.basename(back.f_code.co_filename or "?"),
                                back.f_lineno,
                            )
                        # Also record the full path of the call site, so the comparator can decide "whether
                        # this hop enters the library from the downstream project's own code". Between huge
                        # libraries (numpy etc.) and the downstream project sit third-party or internal frames
                        # such as scipy, importlib and array_function dispatch; their return value differences
                        # are useless for localization.
                        caller_path = ""
                        if back is not None:
                            caller_path = back.f_code.co_filename or ""
                        rec = {
                            "i": _seq[0],
                            "fn": _qualname(code),
                            "file": os.path.basename(code.co_filename or "?"),
                            "caller": caller,
                            "caller_path": caller_path,
                            "args": args,
                        }
                        _seq[0] += 1
                self._stack().append(rec)
            elif event == "return":
                st = self._stack()
                if st:
                    rec = st.pop()
                    if rec is not None:
                        rec["ret"] = _safe_repr(arg)
                        _records.append(rec)
                        if len(_records) >= _MAX:
                            _truncated[0] = True
        except Exception:
            pass  # the recorder must never affect the tests


_profiler = _Profiler()


def _install():
    if not _PKGS:
        return
    _resolve_pkg_dirs()
    threading.setprofile(_profiler)
    sys.setprofile(_profiler)


def _integrity_check():
    """Library module integrity check (catches monkey patching): after the tests finish, check whether
    callable attributes of the library's modules have been replaced by code defined in files of the
    downstream project. Legitimate code never puts its own functions into the namespace of a library
    module; any hit is hard evidence of monkey patching or wrapping the library."""
    hits = []
    cwd = os.getcwd() + os.sep
    for name, mod in list(sys.modules.items()):
        if mod is None:
            continue
        top = name.split(".")[0]
        if top not in _PKGS:
            continue
        try:
            items = list(vars(mod).items())
        except Exception:
            continue
        for attr, val in items:
            code = getattr(val, "__code__", None)
            if code is None:
                func = getattr(val, "__func__", None)
                code = getattr(func, "__code__", None)
            if code is None:
                continue
            fn = code.co_filename or ""
            if fn.startswith(cwd) and "/site-packages/" not in fn:
                hits.append({"module": name, "attr": attr,
                             "defined_in": os.path.relpath(fn, cwd)})
                if len(hits) >= 20:
                    return hits
    return hits


def _attach_status():
    """Whether the recorder actually attached.

    This signal is required, for exactly the same reason as on the JavaScript side: "the two versions
    show no behavior difference" and "the recorder never attached" look identical in the output, both
    are zero records. Without this field, an injection failure would be read by the caller as a
    "no behavior difference" conclusion, which is silently producing a wrong answer.

    The criterion is "the library package was really imported and its real file was located", not
    that _PKGS is non-empty: _resolve_pkg_dirs() also appends fallback path-substring patterns when
    the import fails, so checking only for non-emptiness would treat the fallback as success."""
    hooked, missing = [], []
    for pkg in _PKGS:
        mod = sys.modules.get(pkg)
        if mod is not None and getattr(mod, "__file__", None):
            hooked.append(pkg)
        else:
            missing.append(pkg)
    return hooked, missing


def _dump():
    if not _PKGS:
        return
    sys.setprofile(None)
    threading.setprofile(None)
    try:
        monkeypatched = _integrity_check()
    except Exception:
        monkeypatched = []
    try:
        hooked, missing = _attach_status()
    except Exception:
        hooked, missing = [], list(_PKGS)
    attached = bool(hooked)
    meta = {
        "_meta": True,
        "eco": "python",
        "pkgs": _PKGS,
        "recorded": len(_records),
        "truncated": _truncated[0],
        "attached": attached,
        "hooked_pkgs": sorted(hooked),
        "missing_pkgs": sorted(missing),
        "monkeypatched": monkeypatched,
    }
    if not attached:
        meta["error"] = ("The boundary-call recorder attached to no library package (none of pkgs=%s was imported). "
                         "An empty record here does not mean that the two versions behave the same; the caller must treat it as a failure." % _PKGS)
    try:
        with open(_OUT, "w", encoding="utf-8") as f:
            for r in _records:
                f.write(json.dumps(r, ensure_ascii=False) + "\n")
            f.write(json.dumps(meta, ensure_ascii=False) + "\n")
    except Exception as e:
        sys.stderr.write("py_boundary_trace: could not write the trace: %s\n" % e)


# ---- pytest plugin hooks ----

def pytest_configure(config):
    _install()


def pytest_unconfigure(config):
    _dump()

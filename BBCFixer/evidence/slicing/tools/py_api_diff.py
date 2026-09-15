#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""Python interface shape diff (the Python equivalent of japicmp).

Compares the public interface of the two source directories at the ast level: top-level classes
and functions of each module, the methods of each class and their parameter lists.
The output format is STRUCTURALLY IDENTICAL to the japicmp text report (MODIFIED/NEW/REMOVED
CLASS + indented member lines), so parse_japicmp_blocks in extract_basis.py parses it unchanged.

Usage: py_api_diff.py <old_src_dir> <new_src_dir>
"""
import ast
import os
import sys


def _sig(fn):
    a = fn.args
    names = [x.arg for x in a.posonlyargs + a.args]
    if a.vararg:
        names.append("*" + a.vararg.arg)
    names += [x.arg for x in a.kwonlyargs]
    if a.kwarg:
        names.append("**" + a.kwarg.arg)
    return f"{fn.name}({', '.join(names)})"


def api_of(root):
    """{relative module name: {"funcs": {name: signature}, "classes": {class name: {method name: signature}}}};
    skips underscore-private names and test directories."""
    api = {}
    for dp, dns, fns in os.walk(root):
        dns[:] = [d for d in dns if d not in ("tests", "test", ".git", "docs", "examples", "benchmarks")]
        for fn in fns:
            if not fn.endswith(".py") or fn.startswith("test_"):
                continue
            path = os.path.join(dp, fn)
            rel = os.path.relpath(path, root)[:-3].replace(os.sep, ".")
            try:
                tree = ast.parse(open(path, encoding="utf-8", errors="ignore").read())
            except SyntaxError:
                continue
            mod = {"funcs": {}, "classes": {}}
            for node in tree.body:
                if isinstance(node, (ast.FunctionDef, ast.AsyncFunctionDef)) and not node.name.startswith("_"):
                    mod["funcs"][node.name] = _sig(node)
                elif isinstance(node, ast.ClassDef) and not node.name.startswith("_"):
                    meths = {}
                    for sub in node.body:
                        if isinstance(sub, (ast.FunctionDef, ast.AsyncFunctionDef)) \
                           and (not sub.name.startswith("_") or sub.name == "__init__"):
                            meths[sub.name] = _sig(sub)
                    mod["classes"][node.name] = meths
            if mod["funcs"] or mod["classes"]:
                api[rel] = mod
    return api


def main(old_root, new_root):
    old, new = api_of(old_root), api_of(new_root)
    out = []

    def emit_class(status, mod, cls, old_m, new_m):
        lines = []
        for name in sorted(set(old_m) - set(new_m)):
            lines.append(f"\t---! REMOVED METHOD: {old_m[name]}")
        for name in sorted(set(new_m) - set(old_m)):
            lines.append(f"\t+++  NEW METHOD: {new_m[name]}")
        for name in sorted(set(old_m) & set(new_m)):
            if old_m[name] != new_m[name]:
                lines.append(f"\t***! MODIFIED METHOD: {old_m[name]} -> {new_m[name]}")
        if status != "MODIFIED" or lines:
            out.append(f"***! {status} CLASS: PUBLIC {mod}.{cls}")
            out.extend(lines)

    mods = sorted(set(old) | set(new))
    for mod in mods:
        o = old.get(mod, {"funcs": {}, "classes": {}})
        n = new.get(mod, {"funcs": {}, "classes": {}})
        # Top-level functions: attached to the pseudo-class "module", keeping the japicmp block structure
        f_lines = []
        for name in sorted(set(o["funcs"]) - set(n["funcs"])):
            f_lines.append(f"\t---! REMOVED METHOD: {o['funcs'][name]}")
        for name in sorted(set(n["funcs"]) - set(o["funcs"])):
            f_lines.append(f"\t+++  NEW METHOD: {n['funcs'][name]}")
        for name in sorted(set(o["funcs"]) & set(n["funcs"])):
            if o["funcs"][name] != n["funcs"][name]:
                f_lines.append(f"\t***! MODIFIED METHOD: {o['funcs'][name]} -> {n['funcs'][name]}")
        if f_lines:
            out.append(f"***! MODIFIED CLASS: PUBLIC {mod}")
            out.extend(f_lines)
        for cls in sorted(set(o["classes"]) - set(n["classes"])):
            emit_class("REMOVED", mod, cls, o["classes"][cls], {})
        for cls in sorted(set(n["classes"]) - set(o["classes"])):
            emit_class("NEW", mod, cls, {}, n["classes"][cls])
        for cls in sorted(set(o["classes"]) & set(n["classes"])):
            emit_class("MODIFIED", mod, cls, o["classes"][cls], n["classes"][cls])

    print("\n".join(out) if out else "(no public interface change; if there is a break it is behavioral, so look at the source diff)")


if __name__ == "__main__":
    main(sys.argv[1], sys.argv[2])

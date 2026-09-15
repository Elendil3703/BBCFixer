#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""Normalise the output of diff -ruN to git-style a/ and b/ prefixes and drop the timestamp
column of the file headers.

Why this is required: without normalisation the evidence carries the executor's scratch
directory name and the run timestamp (of the form
for example /home/user/work/case/.bbcfixer/up-scratch/src-NEW/package/index.js). That is a needless
path leak, and it also makes the evidence of two runs of the same case differ byte for byte,
whereas "running the same code at the same version twice must produce the same output" is
the premise the whole differential setup rests on.

Usage: normdiff.py <diff file> <old root> <new root>   result goes to standard output
"""
import re
import sys


def main():
    path, oldroot, newroot = sys.argv[1:4]
    try:
        fh = open(path, encoding="utf-8", errors="replace")
    except OSError:
        return
    out = []
    for line in fh:
        line = line.rstrip("\n")
        if line.startswith("--- "):
            body = line[4:].split("\t")[0]
            out.append("--- a/" + body[len(oldroot) + 1:]
                       if body.startswith(oldroot + "/") else "--- " + body)
        elif line.startswith("+++ "):
            body = line[4:].split("\t")[0]
            out.append("+++ b/" + body[len(newroot) + 1:]
                       if body.startswith(newroot + "/") else "+++ " + body)
        elif line.startswith("diff ") or line.startswith("Only in "):
            line = re.sub(re.escape(newroot) + "/", "b/", line)
            line = re.sub(re.escape(oldroot) + "/", "a/", line)
            out.append(line)
        else:
            out.append(line)
    sys.stdout.write("\n".join(out) + ("\n" if out else ""))


if __name__ == "__main__":
    main()

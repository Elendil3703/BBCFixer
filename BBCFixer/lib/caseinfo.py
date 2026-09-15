#!/usr/bin/env python3
"""caseinfo.py <meta.json>: prints shell assignments derived from a BBCBench case.

    HARNESS    image | lockfile
    PROJ       the downstream project name shown in the prompt
    KIND       upgrade kind of image cases (single-package | dated-snapshot)
    MIG_DATE   the version label shown in the prompt
    DEPS       the upgraded libraries as "name==v_old==v_new" words (image cases)
    KEY_DEPS   the upgraded libraries as shown in the prompt, e.g. "marshmallow==4.0.0, netaddr==1.3.0"
"""
import json
import shlex
import sys

m = json.load(open(sys.argv[1], encoding="utf-8"))
u = m.get("upgrade") or {}
harness = m.get("harness", "")
proj = (m.get("downstream") or {}).get("repo") or m.get("repo") or m["id"]
proj = proj.split("/")[-1]
deps = u.get("key_deps") or [{"name": m["lib"], "v_old": m["v_old"], "v_new": m["v_new"]}]


def q(k, v):
    print("%s=%s" % (k, shlex.quote("" if v is None else str(v))))


q("HARNESS", harness)
q("PROJ", proj)
if harness == "image":
    kind = u.get("kind", "single-package")
    q("KIND", kind)
    if kind == "dated-snapshot":
        q("MIG_DATE", u.get("broken_date", ""))
    else:
        q("MIG_DATE", "%s %s->%s" % (m["lib"], m["v_old"], m["v_new"]))
    q("DEPS", " ".join("%s==%s==%s" % (d["name"], d["v_old"], d["v_new"]) for d in deps))
    q("KEY_DEPS", ", ".join("%s==%s" % (d["name"], d["v_new"]) for d in deps))
else:
    q("KIND", "")
    q("MIG_DATE", m["v_new"])
    q("DEPS", "%s==%s==%s" % (m["lib"], m["v_old"], m["v_new"]))
    q("KEY_DEPS", "%s@%s" % (m["lib"], m["v_new"]))

#!/usr/bin/env python3
"""Runs mini-swe-agent on one prepared BBCBench workspace (the repair step of BBCFixer).

Every parameter comes from environment variables; run-agent.sh sets them from the case.

    MINI_WORK       workspace directory mounted at /work inside the container
    MINI_GIT_DIR    the git repository inside the workspace (tag `baseline` = broken state)
    MINI_CWD        working directory inside the container (default /work)
    MINI_IMG        docker image the agent works in (the case image / runtime image)
    MINI_SAVE       case id
    MINI_PROJ       project name shown in the prompt
    MINI_TEST_CMD   test command shown in the prompt
    MINI_KEY_DEPS   upgraded libraries shown in the prompt, e.g. "packaging==26.2"
    MINI_MIG_DATE   version label shown in the prompt
    MINI_LANG       project language shown in the prompt (default Python)
    MINI_MANIFEST   manifest files named in the "do not edit" rule
    MINI_RUN_ARGS   extra `docker run` arguments, split on whitespace
    MINI_CONFIG     agent configuration (default configs/bbc-our.yaml)
    MINI_MODEL      model name (default qwen/qwen3-coder, via OpenRouter)
    EVIDENCE_DIR    evidence directory, mounted read-only at /evidence
    INIT_ERROR_FILE the broken-state error output shown in the prompt
    RESULTS_DIR     where the trajectory, patch and summary are written
    STEP_LIMIT / WALL_SECONDS / COST_LIMIT / CMD_TIMEOUT   budget overrides
    OPENROUTER_PROVIDER / MAX_TOKENS / MINI_TEMPERATURE    model overrides
    OPENROUTER_API_KEY  the API key (or put it in <package root>/secrets.env)

The agent works inside the image; the workspace is mounted at /work. It runs the tests, reads
the code and edits files by itself. This script does not judge; judge with the BBCBench harness.
"""

import json
import os
import re
import signal
import subprocess
import sys
import time
from pathlib import Path

import yaml

os.environ.setdefault("MSWEA_SILENT_STARTUP", "1")
# Retry limit for failed model calls. The default (10 attempts with exponential backoff)
# can spin for hours when the network is down.
os.environ.setdefault("MSWEA_MODEL_RETRY_STOP_AFTER_ATTEMPT", "4")

from minisweagent.agents.default import DefaultAgent  # noqa: E402
from minisweagent.environments.docker import DockerEnvironment  # noqa: E402
from minisweagent.models import get_model  # noqa: E402

HERE = Path(__file__).resolve().parent
ROOT = HERE.parent


_STOP = set("""assert where and the of not with object instances between supported does apply type types value values
different failed error errors line lines file files self none true false raise raised return returns from import
test tests expected actual got given call called args kwargs result results data item items list dict str int
python site packages lib local usr home work src module function class method attribute name names key keys
because should would could must while when then than that this these those there here into onto over under
""".split())
_KW_RE = re.compile(r"[A-Za-z_][A-Za-z0-9_]{2,}")
# Names that are common in error output but useless for retrieval: project, library and framework
# names, and words from the internals of pytest and pandas assertions.
_GENERIC_NAMES = set("""numpy pandas scipy django werkzeug flask skimage sklearn matplotlib pytest unittest testing
libs pretty ugly right left index array series dataframe hello world foo bar baz descriptor objects doesn
utils core algorithms approximation site-packages which equal except cannot select instead ensure support
operations python3 perform compare suppress usage deprecated attempt understand browser server proxy""".split())
_GENERIC_ERR = re.compile(r"^(?:[A-Z][A-Za-z]+(?:Error|Exception|Warning))$")


def symptom_keywords(init_error: str, cap: int = 24) -> list:
    """Extracts keywords from the error output mechanically: exception names, quoted names,
    dtype words and long identifiers on the E lines."""
    if not init_error:
        return []
    kws, seen = [], set()
    def add(k):
        k = k.strip("._'\"`")
        if len(k) < 3 or k.lower() in _STOP or k in seen:
            return
        if re.match(r"^x?[0-9a-f]{6,}$", k, re.I) or k.startswith("test_") or k.lower() in _GENERIC_NAMES:
            return
        seen.add(k); kws.append(k)
    for m in re.finditer(r"\b([A-Z][A-Za-z]+(?:Error|Exception|Warning))\b", init_error):
        add(m.group(1))
    for m in re.finditer(r"['`\"]([A-Za-z_][\w.]{2,})['`\"]", init_error):
        add(m.group(1).split(".")[-1])
    for m in re.finditer(r"\b(float16|float32|float64|int8|int16|int32|int64|uint8|uint16|uint32|uint64|bool_|object|bytes|datetime64|timedelta64|category)\b", init_error):
        add(m.group(1))
    e_lines = [l for l in init_error.splitlines() if re.match(r"^\s*E\s", l) or "Error" in l]
    for l in e_lines[:12]:
        for m in _KW_RE.finditer(l):
            w = m.group(0)
            if len(w) >= 5 and not w.isupper():
                add(w)
        if len(kws) >= cap:
            break
    return kws[:cap]


def symptom_hits(evidence_dir: str, init_error: str, budget: int = 2200) -> str:
    """Finds the fragments of the sliced diff that contain the most symptom keywords (a mechanical
    search, no judgement). Fragments from the library tests (test-diff.txt) rank above fragments
    from the library source (source-diff.txt)."""
    kws = symptom_keywords(init_error)
    if not kws or not evidence_dir:
        return ""
    root = Path(evidence_dir)
    if not root.is_dir():
        return ""
    depnames = {d.name.lower() for d in root.iterdir() if d.is_dir()}
    kws = [k for k in kws if k.lower() not in depnames]
    if not kws:
        return ""
    pats = [(k, re.compile(r"(?<![A-Za-z0-9_])" + re.escape(k) + r"(?![A-Za-z0-9_])", re.I)) for k in kws]
    windows = []
    # Only the code-side evidence is searched: the revised library tests and the source diff.
    for fname, weight in (("test-diff.txt", 2), ("source-diff.txt", 1)):
        files = sorted(root.rglob(fname))
        for f in files:
            try:
                lines = f.read_text(errors="replace").splitlines()
            except Exception:
                continue
            body = []
            for l in lines:
                if l.startswith(("diff -", "--- ", "+++ ", "@@", "index ", "# ")):
                    body.append("")
                    continue
                body.append(l[1:] if l[:1] in "+- " else l)
            n = len(body)
            i = 0
            while i < n:
                hit = {k for k, p in pats if body[i] and p.search(body[i])}
                if hit:
                    lo, hi = max(0, i - 3), min(n, i + 4)
                    j = i + 1
                    while j < hi:
                        h2 = {k for k, p in pats if body[j] and p.search(body[j])}
                        if h2:
                            hit |= h2; hi = min(n, j + 4)
                        j += 1
                    seg = [x for x in body[lo:hi] if x.strip()]
                    if seg:
                        strong = [k for k in hit if not _GENERIC_ERR.match(k)]
                        generic = [k for k in hit if _GENERIC_ERR.match(k)]
                        # A fragment that only matches generic exception names (TypeError ...) has
                        # almost no locating value.
                        score = weight * (2.0 * len(strong) + 0.3 * len(generic)) - 0.02 * len(seg)
                        if not strong:
                            score -= 5
                        windows.append((score, fname, f.parent.name, lo + 1, "\n".join(seg), sorted(strong) + sorted(generic)))
                    i = hi
                else:
                    i += 1
    if not windows:
        return ""
    windows.sort(key=lambda w: -w[0])
    out, used = [], 0
    for score, fname, dep, ln, seg, hit in windows:
        if score <= 0:
            break
        seg = seg[:650]
        if used + len(seg) > budget:
            continue
        out.append("[%s · %s from line %d · matches %s]\n%s" % (dep, fname, ln, ", ".join(hit[:6]), seg))
        used += len(seg)
        if len(out) >= 4:
            break
    if not out:
        return ""
    src_label = "the library test changes and source diff"
    return ("Fragments of the library code evidence that match the most error keywords (mechanical search, for locating; from %s). " % src_label +
            "Keywords = " + ", ".join(kws[:12]) + "\n\n" + "\n\n".join(out))


def evidence_digest(evidence_dir: str, init_error: str = "") -> str:
    """The opening digest injected into the prompt: the evidence guide, the first divergence from
    behavior-diff.md (or the explicit statement that none was found) and the symptom-matched
    fragments. Pure text extraction, no judgement. Works with both evidence layouts (image cases:
    one subdirectory per library; lockfile cases: flat)."""
    if not evidence_dir:
        return ""
    root = Path(evidence_dir)
    if not root.is_dir():
        return ""
    parts = []
    guides = sorted(root.rglob("evidence-guide.md"))
    if guides:
        t = guides[0].read_text(errors="replace").strip()
        if t:
            parts.append(t[:1200])
    diffs = sorted(root.rglob("behavior-diff.md"))
    if diffs:
        t = diffs[0].read_text(errors="replace")
        i = t.find("★")
        if i >= 0:
            seg = t[i:]
            # Only the starred item itself: up to the next top-level list item or heading.
            stops = [j for j in (seg.find(s, 1) for s in ("\n- `", "\n## ", "\n# ")) if j > 0]
            if stops:
                seg = seg[:min(stops)]
            parts.append("First divergence (measured by differential execution: the earliest call from the downstream project's own code into the library whose return value differs):\n"
                         + seg.strip()[:2200])
        elif "No return-value difference was observed on the boundary calls of the downstream project's own code" in t:
            parts.append("Differential execution: no return-value difference was observed on the boundary calls of the "
                         "downstream project's own code (a blind spot of the recorder, typically rule-like changes such as "
                         "type promotion, str versus bytes, time zones or default arguments). Do not look for a first "
                         "divergence under /evidence; rely on the failing assertion and the keyword-matched fragments below.")
    hits = symptom_hits(evidence_dir, init_error)
    if hits:
        parts.append(hits)
    return "\n\n".join(parts).strip()


def load_config(path: Path) -> dict:
    """Loads a configuration file; `extends: <file>` inherits from another file (deep merge).

    bbc-our.yaml extends bbc-baseline.yaml and overrides only instance_template, so the budget,
    model and environment parameters cannot drift between the two."""
    def deep_merge(base: dict, over: dict) -> dict:
        out = dict(base)
        for k, v in over.items():
            if isinstance(v, dict) and isinstance(out.get(k), dict):
                out[k] = deep_merge(out[k], v)
            else:
                out[k] = v
        return out

    cfg = yaml.safe_load(path.read_text()) or {}
    parent = cfg.pop("extends", None)
    if parent:
        parent_path = (path.parent / parent).resolve()
        return deep_merge(load_config(parent_path), cfg)
    return cfg


def load_secrets():
    """Reads <package root>/secrets.env (KEY=value lines) into the environment, if present."""
    cand = ROOT / "secrets.env"
    if not cand.exists():
        return
    for line in cand.read_text().splitlines():
        line = line.strip()
        if not line or line.startswith("#") or "=" not in line:
            continue
        k, v = line.split("=", 1)
        os.environ.setdefault(k.strip(), v.strip().strip("\"'"))


def main():
    lang = os.getenv("MINI_LANG", "Python")
    manifest_names = os.getenv("MINI_MANIFEST", "requirements / setup.py / setup.cfg")
    extra_run_args = os.getenv("MINI_RUN_ARGS", "").split()

    work = Path(os.environ["MINI_WORK"]).resolve()
    if not work.is_dir():
        sys.exit(f"workspace not found: {work}")
    save = os.environ["MINI_SAVE"]
    proj = os.getenv("MINI_PROJ", save)
    image = os.environ["MINI_IMG"]
    test_cmd = os.getenv("MINI_TEST_CMD", "npm test")
    key_deps = os.getenv("MINI_KEY_DEPS", "(see the project's dependency declarations)")
    mig_date = os.getenv("MINI_MIG_DATE", "")

    cfg_path = Path(os.getenv("MINI_CONFIG") or (HERE / "configs" / "bbc-our.yaml"))
    cfg = load_config(cfg_path)
    agent_cfg = dict(cfg.get("agent", {}))
    env_cfg = dict(cfg.get("environment", {}))
    model_cfg = dict(cfg.get("model", {}))

    if os.getenv("STEP_LIMIT"):
        agent_cfg["step_limit"] = int(os.environ["STEP_LIMIT"])
    if os.getenv("WALL_SECONDS"):
        agent_cfg["wall_time_limit_seconds"] = int(os.environ["WALL_SECONDS"])
    if os.getenv("COST_LIMIT"):
        agent_cfg["cost_limit"] = float(os.environ["COST_LIMIT"])
    if os.getenv("CMD_TIMEOUT"):
        env_cfg["timeout"] = int(os.environ["CMD_TIMEOUT"])
    if os.getenv("MINI_CWD"):
        env_cfg["cwd"] = os.environ["MINI_CWD"]
    # The working directory named in the prompt must be the one used inside the container.
    work_dir = env_cfg.get("cwd", "/work")
    if os.getenv("OPENROUTER_PROVIDER"):
        model_cfg.setdefault("model_kwargs", {})["provider"] = {
            "order": [os.environ["OPENROUTER_PROVIDER"]],
            "allow_fallbacks": False,
        }
    if os.getenv("MAX_TOKENS"):
        model_cfg.setdefault("model_kwargs", {})["max_tokens"] = int(os.environ["MAX_TOKENS"])
    if os.getenv("MINI_TEMPERATURE") not in (None, ""):
        model_cfg.setdefault("model_kwargs", {})["temperature"] = float(os.environ["MINI_TEMPERATURE"])

    load_secrets()
    model_name = os.getenv("MINI_MODEL", "qwen/qwen3-coder")
    # The OpenRouter model class sends model_name to the API as is; drop a litellm-style prefix.
    if model_cfg.get("model_class") == "openrouter" and model_name.startswith("openrouter/"):
        model_name = model_name[len("openrouter/"):]

    results = Path(os.getenv("RESULTS_DIR") or (HERE / "results"))
    results.mkdir(parents=True, exist_ok=True)
    stamp = time.strftime("%Y%m%d-%H%M%S")
    tag = f"{save}-{cfg_path.stem}-{re.sub(r'[/:]', '_', model_name)}-{stamp}"
    traj_path = results / f"{tag}.traj.json"

    print(f">>> [{proj}] case: {save}")
    print(f">>> [{proj}] image: {image}   workspace: {work}")
    print(f">>> [{proj}] config: {cfg_path.name}   model: {model_name}")
    print(f">>> [{proj}] budget: steps<={agent_cfg.get('step_limit')} wall<={agent_cfg.get('wall_time_limit_seconds')}s "
          f"cost<=${agent_cfg.get('cost_limit')} command timeout={env_cfg.get('timeout')}s")
    print(f">>> [{proj}] trajectory: {traj_path}")

    run_args = ["--rm", "-v", f"{work}:/work"]
    # --add-host conflicts with `--network container:<sidecar>` (cases with a database sidecar).
    if not any("container:" in a for a in extra_run_args):
        run_args.append("--add-host=host.docker.internal:host-gateway")
    run_args += ["--entrypoint", ""] + extra_run_args
    # The evidence is generated on the host; the agent works in the container, so the directory
    # is mounted read-only at /evidence and the agent reads it as it sees fit.
    evidence_dir = os.getenv("EVIDENCE_DIR", "")
    if evidence_dir:
        ev = Path(evidence_dir).resolve()
        if not ev.is_dir():
            sys.exit(f"EVIDENCE_DIR not found: {ev}")
        run_args += ["-v", f"{ev}:/evidence:ro"]
        print(f">>> [{proj}] evidence (read-only): {ev} -> /evidence")
    env = DockerEnvironment(image=image, run_args=run_args, **env_cfg)
    model = get_model(model_name, model_cfg)
    agent = DefaultAgent(model, env, output_path=traj_path, **agent_cfg)

    # Hard wall-clock watchdog. The agent checks its wall-time limit only before each model call;
    # a call stuck in network retries never returns to that check, so an alarm is set as well.
    hard_wall = int(agent_cfg.get("wall_time_limit_seconds") or 0)
    if hard_wall > 0:
        def _on_alarm(signum, frame):
            raise TimeoutError(f"HardWallTimeout: not finished after {hard_wall + 300}s")
        signal.signal(signal.SIGALRM, _on_alarm)
        signal.alarm(hard_wall + 300)

    start = time.time()
    try:
        init_error = ""
        if os.getenv("INIT_ERROR_FILE") and Path(os.environ["INIT_ERROR_FILE"]).exists():
            init_error = Path(os.environ["INIT_ERROR_FILE"]).read_text()[:6000]
        result = agent.run(
            task="",
            proj=proj,
            key_deps=key_deps,
            mig_date=mig_date or "pinned",
            test_cmd=test_cmd,
            lang=lang,
            manifest_names=manifest_names,
            work_dir=work_dir,
            init_error=init_error,
            evidence_mounted=bool(evidence_dir),
            evidence_digest=evidence_digest(evidence_dir, init_error),
            evidence_level="full",
            upstream_mounted=False,
        )
        exit_status = result.get("exit_status", "")
    except Exception as e:  # noqa: BLE001
        exit_status = f"Error: {type(e).__name__}: {str(e)[:200]}"
    finally:
        if hard_wall > 0:
            signal.alarm(0)
        env.cleanup()
    elapsed = int(time.time() - start)

    patch_path = results / f"{tag}.patch"
    git_dir = Path(os.getenv("MINI_GIT_DIR") or work)
    # Stage new files before exporting: an agent sometimes fixes a case by creating a file, and a
    # diff of tracked files only would miss such a repair entirely.
    subprocess.run(["git", "add", "-A"], cwd=git_dir, capture_output=True)
    # The prompt tells the agent to back up a file as *.orig before editing it; backups are not part
    # of the repair and are excluded from the exported patch.
    diff = subprocess.run(
        ["git", "diff", "baseline", "--", ".",
         ":(exclude)*.orig", ":(exclude)*.bak", ":(exclude)*.rej"],
        cwd=git_dir, capture_output=True, text=True)
    patch_path.write_text(diff.stdout)

    # The providers that actually served the calls, so the pinning can be checked afterwards.
    providers = sorted({
        str(m.get("extra", {}).get("response", {}).get("provider"))
        for m in agent.messages if m.get("role") == "assistant"
    } - {"None"})

    summary = {
        "save": save, "proj": proj, "image": image,
        "config": cfg_path.name, "model": model_name,
        "lang": lang, "test_cmd": test_cmd,
        "providers": providers,
        "step_limit": agent_cfg.get("step_limit"),
        "exit_status": exit_status,
        "n_calls": agent.n_calls, "cost": round(agent.cost, 4),
        "elapsed_seconds": elapsed,
        "patch_bytes": len(diff.stdout),
        "trajectory": str(traj_path), "patch": str(patch_path),
    }
    (results / f"{tag}.summary.json").write_text(json.dumps(summary, ensure_ascii=False, indent=2))
    print(f">>> [{proj}] finished: {exit_status} | steps {agent.n_calls} | ${agent.cost:.4f} | {elapsed}s "
          f"| patch {len(diff.stdout)} bytes")
    print("SUMMARY " + json.dumps(summary, ensure_ascii=False))


if __name__ == "__main__":
    main()

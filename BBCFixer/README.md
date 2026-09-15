# BBCFixer

Repairs a behavioral breaking change by building evidence from the upgrade itself and handing it to a repair agent. It runs on the cases of [BBCBench](../BBCBench).

## What is here

| Path | What it holds |
|---|---|
| `run.sh` | one case end to end: prepare the broken state, build the evidence, run the agent, judge |
| `evidence/` | evidence generation; `gen.sh` picks `image.sh` or `lockfile.sh` by the harness of the case |
| `evidence/diffexec/` | differential execution: runs the failing test in the intact and the broken state, records every call into the library, compares the two runs |
| `evidence/slicing/` | library diff filtering: fetches both library versions and slices their source and test diffs down to the candidate root API |
| `agent/` | the repair agent: mini-swe-agent configurations and the runner |
| `agent/prompts-en/` | English renderings of the prompts, for reading |
| `lib/`, `patches/` | a reader for `meta.json`; one patch to mini-swe-agent that the experiments needed |

`run.sh` performs the three steps of the approach in order. Sections refer to the paper.

1. **Differential execution** (Section 3.2.1) runs the failing test twice, with the library at `v_old` and at `v_new`, recording every call from the downstream project into the library with its arguments and its return value. The two runs are aligned and compared. The highest-ranked call whose return value differs is the **first divergence**, and its API is the candidate root API.
2. **Library diff filtering** (Section 3.2.2) slices the diff between the two library versions down to the changed lines that mention the candidate root API or a symptom symbol from the error message.
3. **Evidence-guided repair** (Section 3.2.3) mounts the evidence read-only at `/evidence`. The opening prompt carries the first divergence and at most four fragments of the sliced diff; the rest stays in the files for the agent to read if it wants. Before reporting completion the agent checks the conditions in `contract.md`.

Judging is done by the BBCBench harness, not by this package.

## Setup

Needs Docker, git, Python 3 and an OpenRouter key. JavaScript cases also need `npm` on the host.

```bash
git clone https://github.com/Elendil3703/BBCFixer.git && cd BBCFixer
cd BBCBench && scripts/get-intact.sh && harness/image/pull-images.sh && cd ../BBCFixer
python3 -m venv .venv && .venv/bin/pip install -r requirements.txt
patch -p1 -d .venv/lib/python3.*/site-packages < patches/mini-swe-agent-openrouter.patch
echo 'OPENROUTER_API_KEY=...' > secrets.env
```

## Use

```bash
./run.sh <id> work/<id>                    # evidence, repair and judgement for one case
./run.sh <id> work/<id> --evidence-only    # stop after the evidence
```

`MINI_MODEL` picks the model, `qwen/qwen3-coder` by default; the paper also runs `z-ai/glm-4.5-air` with `MINI_CONFIG=agent/configs/bbc-our-glm.yaml`. A run stops at 100 steps, 30 minutes or 4 dollars, whichever comes first. The trajectory, the patch and a summary land in `work/<id>/.bbcfixer/agent/`.

The evidence of a case is in `work/<id>/evidence/`:

| File | What it holds |
|---|---|
| `behavior-diff.md` | the measured behavior difference; the first divergence is marked `★` |
| `code-diff-slice.txt` | the sliced library source diff (named `source-diff.txt` in image cases) |
| `test-diff.txt` | the sliced library test diff: the expectations the library maintainers rewrote |
| `contract.md` | the repair contract: declared items from the test diff, measured items from the divergences, and the judging rule |
| `entry-symbols.txt` | which symbols the diff was sliced by, and where each of them came from |
| `evidence-guide.md` | which fragments each symptom symbol matched |

## Configurations

`agent/configs/bbc-our.yaml` is BBCFixer. It inherits every budget, model and environment parameter from `bbc-baseline.yaml`, the baseline setting of the paper, and overrides only the prompt, so the two settings can differ in nothing but the injected evidence. `bbc-our-glm.yaml` is the same prompt with one added line, used with GLM-4.5-Air.

The prompts are in Chinese because that is what the experiments used. `agent/prompts-en/` holds English renderings of both; they are for reading and are never sent to a model.

## Limits

- The boundary-call recorder covers Python calls and JavaScript `require` and `import`. It cannot see C extension functions, and it cannot attach under jest, which uses its own module registry.
- When the recorder captures nothing comparable, the case runs on the library diff alone and the contract says so. An empty recording never means that the two versions behave the same.
- `evidence/diffexec/` also carries a post-repair probe. It is observational and takes no part in judging.

## License

Apache-2.0 (`../LICENSE`). The agent framework is [mini-swe-agent](https://github.com/SWE-agent/mini-swe-agent) (MIT), installed from PyPI.

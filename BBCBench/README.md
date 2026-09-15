# BBCBench

100 real dependency upgrades that break a downstream project through a **behavioral breaking change**: the library keeps its interface but changes its run-time behavior, so the break shows up as a failing test, not a compile error. Every case ships a reproducible harness and a reference fix.

| | |
|---|---|
| Cases | 100 (41 Python, 59 JavaScript) |
| Reference fix from | benchmark authors (11), downstream maintainers (29), library documentation (60) |
| States per case | **intact** (old library, tests pass), **broken** (upgraded library, tests fail), **fixed** (reference fix applied, tests pass) |
| Task for an agent | edit the downstream code so the tests pass with the upgraded library; downgrading is not allowed |

`cases.csv` lists every case. `cases/<id>/meta.json` describes one case in plain English: what changed in the library, how the downstream fails, what the reference fix does, and the labeled root API.

## Setup

Needs Docker, git and Python 3. JavaScript cases also need `npm` on the host.

```bash
git clone https://github.com/Elendil3703/BBCFixer.git && cd BBCFixer/BBCBench
scripts/get-intact.sh          # source snapshots of the 40 image cases (350 MB)
harness/image/pull-images.sh   # their prebuilt images (about 80 GB in total; pass case ids to pull a few)
```

## Two kinds of harness

`meta.json` says which one a case uses (`"harness": "image"` or `"lockfile"`). The commands are the same.

- **image** (40 Python cases): the broken environment is a prebuilt Docker image; the source snapshot lives in `cases/<id>/intact/`.
- **lockfile** (60 cases): a public `node:<N>` or `python:3.10` image plus a pinned lockfile; the harness clones the downstream commit and installs exactly the recorded dependency tree.

## Use

```bash
H=harness/image      # or harness/lockfile, see meta.json
$H/verify.sh <id>                 # replay the states and check them
$H/prepare.sh <id> work/<id>      # build the broken state as a workspace for your agent
# ... let your agent edit work/<id>/src (work/<id>/repo for the pip case) ...
$H/judge.sh <id> work/<id>        # PASS or FAIL
```

The workspace holds the project, `run_tests.sh` (runs the tests in the pinned environment) and `_FAILING.txt` (the failing output). It contains no reference fix and no metadata.

The judge discards every edit to test files, applies the reference-fix test state, checks that the library is still at `v_new` and that the dependency tree and manifests are unchanged, then reruns the tests. A run passes when the tests pass and at least one test actually ran.

## Case layout

```
cases/<id>/
  meta.json                 facts and English description (see fields below)
  reference_fix.patch       the reference fix (production code only)
  image cases:  intact/  Dockerfile  harness.env  test_files.txt  installed_packages.txt  [reference_fix_tests.patch]
  lockfile cases: pinned/  (lockfiles, dependency trees, checksums, archived STATE-OLD/NEW/FIXED logs)
```

Key `meta.json` fields: `lib`, `v_old`, `v_new`, `downstream.repo`, `downstream.commit`, `reference_fix_source`, `root_api`, `behavioral_change`, `downstream_effect`, `reference_fix`, `documented_in` (lockfile cases), `failing_tests` (lockfile cases), `runtime.image`.

## License

Harness code and metadata: Apache-2.0. The 11 benchmark-author cases derive from [TimeMachine-bench](https://github.com/fujii-lab/TimeMachine-bench) (Apache-2.0). Downstream source snapshots keep their own licenses.

English rendering of `configs/bbc-our.yaml` (`instance_template`).
The experiments used the Chinese original; this file is for reading only.

Jinja placeholders (`{{...}}`, `{% ... %}`) are kept exactly as in the YAML; the original's 【...】 emphasis is rendered in bold.

---

You are the maintainer of the {{lang}} project `{{proj}}`. The project's dependencies have been upgraded and **pinned** to a set of new versions; the key dependencies are {{key_deps}} (version identifier: {{mig_date}}).
Under this pinned set of dependencies, the project's tests currently fail.

Your task: modify the **project source code** so that it passes the tests again on these pinned dependency versions.

## Proceed in the following five stages (state the judgement of each stage in one or two sentences; do not restate at length)

Stage 1: Collect the symptoms. Run the tests (or read the captured error output below directly) and group the failures into a number of independent symptoms. For each one, note: where the error occurs, its type, and the concrete asserted values (what was expected, what was actually obtained).
Note that the place where the error is reported is often not the place that needs to be modified.

Stage 2: Locate the upstream change. Follow this order of priority:
(1) First read the failing assertion itself: on which attribute do the expected and actual values differ (type, precision, string versus bytes, time zone, whether an exception is raised, whether None or a value is returned)? This almost always tells you "what changed".
(2) Then look at the "error-keyword-matched fragments" in the opening digest: these are verbatim excerpts retrieved mechanically from the upstream test changes and the upstream source diff. An assertion that upstream rewrote states directly what this behavior was changed to (the new expected value); the source diff states why it changed.
(3) If the opening digest gives a "first divergence" (★), it is the earliest point, measured by differential execution, at which a return value changed when the downstream code calls upstream. It can be used to confirm "where the change enters the downstream project". It is a candidate clue: when it contradicts the assertion, the assertion wins. If the digest states that "no return-value difference was observed at the boundary calls of the downstream project's own code", do not go looking for a first divergence under /evidence; there is none.
(4) Only when the above is still insufficient to locate the change, go into /evidence and read the test diff (which existing assertions upstream rewrote, and what they were changed to) and the source diff in detail.

A hint for wholesale upgrades of large foundational libraries (numpy, pandas, scipy, Django, werkzeug, requests and the like):
what such upgrades break is usually a "rule", not a function that disappeared: type promotion rules (a float32 array combined with a float64 scalar now yields float64), strings now read as bytes by default, changed default time zones or default parameters, a silent return that now raises, a value that is now returned as None. The fix almost always consists of compensating explicitly at the place in the downstream project that "produces the asserted value". Do not modify the upstream library, and do not expect to find "a function that was renamed".

The direction of the compensation (the easiest thing to get backwards; always check against the assertion): the tests did not change, so **the expected value in the assertion is the old behavior** and **the actual value is the new behavior**. Your goal is to make the downstream project produce the expected value again, not to make the code "conform better to the new defaults".
For example: expected float32, actual float64: at the place that produces the result, explicitly cast the result, or the scalars and weights involved in the computation, to **the dtype of the input** (float32), not to float64. Expected str, actual bytes: decode. Expected some value, actual an exception or None: handle this new form at the call site according to the old semantics (catch and return the old result; on None, follow the old default), **preserving the original control flow**, without introducing new early returns.
Keep the change small: preferably compensate only on the one or two lines that produce the value or raise the exception; do not rewrite a whole function, do not delete and re-insert a block of more than ten lines, and do not write several debugging scripts in a row for investigation (at most two).

Stage 3: Determine the adaptation point. Decide that what should be modified is the underlying mechanism that produces the tested attribute, not a patch at the error site that masks the symptom. The new upstream behavior cannot be changed; when the downstream project still needs the old output or behavior, it must be re-implemented compensatingly in the downstream code (convert the return value, rebuild the object, validate explicitly, and so on).
Do not conclude that "the remaining failures are a problem of the tests and cannot be solved in production code": scoring accepts only the original tests, and blaming the tests amounts to giving up.

Stage 4: Implement the change. Modify production code only. Keep the change as small as possible and touch as few places as possible.

Stage 5: Verify behavioral closure. Check: do all failing tests now pass (run the full test command once at the end)? Does the change land at the mechanism level rather than the symptom level? Has any new behavioral deviation been introduced? When the evidence directory contains `contract.md`, check only the entries related to the failure symptoms of this task (in the repair contract of a large library most entries are unrelated to this project; skip the unrelated entries, do not read through them one by one).

## Step economy (the total number of steps is limited; allocate them as follows)
- After each change, re-run only the few failing test cases (for pytest, `path::case_name` or a `-k` expression); do not run the full suite every time. Leave the full run for the final confirmation.
- If three consecutive changes at the same place make no progress, stop and go back to (1) and (2) of Stage 2 to re-analyze; do not keep piling patches on the same place.
- Within roughly 60 steps you should have a first passing version, or have clearly switched to a new direction.

## Environment
- The project code is in `{{work_dir}}`; all of your commands run inside the container, and the dependencies are already installed at the versions above.
- Test command: `{{test_cmd}}`
- Each command runs in a fresh sub-shell; `cd` and environment variables are not preserved. When needed, write `cd {{work_dir}} && ...` or `VAR=value cmd`.

## Current test error (captured from one actual run)
```
{{ init_error if init_error else "（未捕获到报错，请自行运行测试命令查看）" }}
```
(The fallback text reads: "No error was captured; run the test command yourself to see it.")

## Upstream change evidence already computed for you
{% if evidence_mounted -%}
{% if evidence_digest -%}
The most condensed fragments have been extracted and placed here directly; there is no need to search the directory:

```
{{ evidence_digest }}
```

{% endif -%}
The full evidence is in the read-only directory `/evidence`. It contains four kinds of diffs (interface, source, test, dependency declaration) together with the differential execution report, the behavioral contract and other files.

Principle of use: the evidence is reference material, not a mandatory step. When the error and the opening digest are already sufficient to locate the problem, start fixing first and use the evidence afterwards to check that the direction is consistent; read the evidence in detail only when they are insufficient.
When you do need to read it in detail, first run `ls -R /evidence` to see which files exist: if there is a `README.md` or `adaptation-basis.md`, read its guide first, then read the remaining files as needed. Do not `cat` a long evidence file in one go (over-long output is truncated and the second half is not shown); read it in segments with `sed -n '1,120p' file`, or use `grep -n 'keyword' file` to locate first and then read the surrounding tens of lines.
If the evidence text mentions script tools (slicing commands of the form *.sh), they do not exist in your container; ignore them and read the files themselves.
The fragments in the opening digest are the verbatim text; there is no need to re-read those items in the directory, re-reading yields no new information.
**Do not spend many consecutive steps browsing the evidence**: if you have browsed for about 6 steps without a conclusion, stop browsing, make a first change based on the information at hand, use the test feedback to narrow the range, and come back to the evidence later as needed.
Over the whole task, the commands that browse the evidence should total no more than about 10.
{%- else -%}
(No evidence material was generated for this case; infer from the error output on your own.)
{%- endif %}

## Hard constraints (violation counts as failure)
- Dependency versions must remain unchanged: no downgrading, no pinning back to an old version, no removing a dependency, no switching to another library to work around it, and no modifying the version declarations in {{manifest_names}}.
- Do not delete tests, skip tests, or comment out failing code; do not add skip / xfail markers; do not loosen, delete or alter assertions. At scoring time every change you make to test files is discarded and the original tests are restored and re-run.
- Do not hollow out production code or mask symptoms by catching exceptions and the like.
- The adaptation must land on the underlying mechanism that produces the tested attribute.

## Research restrictions
- Do not consult the online resources of this project ({{proj}}) itself, including its repository, commit history, branches, tags, Pull Requests, Issues and Release Notes.
- You may consult the official documentation, release notes and API change notes of the third-party libraries it depends on (such as {{key_deps}}).

## How to finish
When everything passes and the Stage 5 verification is complete, end the task with the following command:
`echo COMPLETE_TASK_AND_SUBMIT_FINAL_OUTPUT`
This command must be issued on its own; do not combine it with other commands.
<important>After this command is issued, no further changes are possible.</important>

## Action format
Every response must contain exactly one `bash` tool call. Common patterns:

- **Do not run `npm install`, `pip install` or any other install or uninstall command, and do not modify {{manifest_names}} or the lockfile (package-lock.json and the like) in any way.** The dependencies are already installed.
  Scoring checks the integrity of the dependency tree and the dependency manifests: even if all tests pass, such changes are judged as failure outright.
- View specific lines: `nl -ba {{work_dir}}/example.py | sed -n '10,30p'`
- Before modifying any file, back it up once: `cp {{work_dir}}/example.py {{work_dir}}/example.py.orig`.
  If you break it, restore from the backup immediately (`cp` it back); do not keep patching a broken file.
- Always modify code with **line-numbered** targeted replacement: first confirm the line number with `nl -ba`, then `sed -i '42s/old/new/' {{work_dir}}/example.py`; when changing several consecutive lines, delete the line range and insert, or write a python script that replaces only the target fragment.
- Whole-file overwriting (`cat <<'EOF' > file`) **is only allowed for small files under 200 lines**. Whole-file overwriting of large files is forbidden: rewriting a long file from memory inevitably loses content and destroys the whole module. Check the line count with `wc -l` before starting.
- **Do not use global replacement without line numbers**, for example `sed -i 's/old/new/g'`. It changes every match in the file, including comments, strings and other identifiers that happen to contain the same characters, and easily turns the file into a syntax error.
- After every modification, immediately inspect the changed place with `nl -ba ... | sed -n` to confirm that the intended line was changed and nothing else was affected; for a Python file additionally run `python -c "import ast; ast.parse(open('file_path').read())"`, for a JavaScript file `node --check file_path`, as a syntax self-check, and continue only once nothing is broken.

---

## Difference in bbc-our-glm.yaml

`configs/bbc-our-glm.yaml` is identical to `configs/bbc-our.yaml` except for one line added immediately after the task statement ("Your task: modify the project source code ..."):

> 不要修改依赖清单与 lockfile（package.json、package-lock.json 等），改了判分直接判负；清单里写的旧版本号不是需要修正的问题。

Translation: Do not modify the dependency manifests or the lockfile (package.json, package-lock.json and the like); if they are changed, scoring counts the run as a failure outright. The old version numbers written in the manifest are not a problem that needs correcting.

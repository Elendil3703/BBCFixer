#!/usr/bin/env python3
"""Repair contract generator (the contract component; shared by both ecosystems).

Mechanically assembles two independent lines of evidence into a "repair contract" (contract.md) whose
items can be checked one by one:
  - the declared channel: the paired assertion changes (old expectation -> new expectation) in the
    sliced test diff of the library; this is the "correct behavior after the upgrade" written down by
    the library maintainers themselves;
  - the measured channel: the diverging boundary calls (old return -> new return) found by differential
    execution, taken from behavior-diff.json.

Contract items come in three kinds:
  C items (declared contract): assertion expectation changes from the test diff;
  D items (measured contract): diverging calls from differential execution. The fix must land on the
      downstream side (no change to the library implementation, no wrapping, no monkey patching, no
      dependency downgrade). No uniform rule is imposed on the return shape of the call after the repair:
      for return-value handling breakages it should agree with v_new; for input/configuration protocol
      breakages, restoring the old business result by adapting through the configuration mechanisms the
      library exposes is in fact the sign of a successful repair. The criterion is where the fix lands,
      not what the return value looks like;
  T items (judging contract): the judging tests must pass under the original assertions (or those frozen
      after the reference fix).

This file only does mechanical extraction and template assembly; it contains no case knowledge and no
model calls. Parts that cannot be extracted are left empty as is and handed to the reviewer
(reviewer.py) for human-level judgment at acceptance.

Usage:
  contract_gen.py --evidence-dir EV --out EV/contract.md \
      [--behavior-json EV/behavior-diff.json] [--symptom symptom.log] \
      [--eco python|java] [--pkg NAME] [--old OLD_VERSION] [--new NEW_VERSION]
"""
import argparse
import json
import os
import re

ASSERT_PAT = {
    "python": re.compile(r"\bassert\b|assertEqual|assertAlmostEqual|assertRaises|pytest\.raises|\.equals\(|== |!= "),
    "java": re.compile(r"assert(Equals|True|False|That|Null|NotNull|Throws|Same)|Assert\.|Assertions\.|expected\s*="),
    # On the JavaScript side all three major styles are recognized: node's built-in assert (assert.deepEqual /
    # strictEqual), chai/jest's expect(...).to... and .toBe/.toEqual, tape's and nodeunit's t.equal /
    # test.deepEqual, plus the should style. Bare === / !== also count, since assertions are often written
    # directly as comparisons.
    # Two more styles: ava's t.is / t.true / t.regex family (the library tests of decamelize use only t.is;
    # without it not a single assertion in the slice would pair up and the C items would be misreported as
    # zero), and expectType of tsd type tests (the expectType<...>(...) in *.test-d.ts is exactly the correct
    # usage of the new signature).
    "javascript": re.compile(
        r"\bassert\b|assert\.|\bexpect\s*\(|\.to\.|\.should\b|should\.|"
        r"\b(?:deepEqual|deepStrictEqual|strictEqual|notEqual|notDeepEqual|equal|equals|ok|throws|ifError)\s*\(|"
        r"\.(?:toBe|toEqual|toStrictEqual|toMatch|toThrow|toHaveBeenCalled\w*)\s*\(|"
        r"\bt\.(?:is|not|true|false|truthy|falsy|regex|notRegex|like|throwsAsync|notThrows|notThrowsAsync)\s*\(|"
        r"\bexpectType\b|"
        r"===|!==|\bexpected\b"),
}
HUNK_RE = re.compile(r"^@@ .*@@")
FILE_RE = re.compile(r"^\+\+\+ (?:b/)?(.*)$")


def pair_assert_changes(test_diff_path, eco, cap=12):
    """Pair up (old assertion line, new assertion line, file) from the sliced test diff.

    Rule: within the same hunk, deleted and added lines that both match the assertion pattern are paired
    one to one in order of appearance; added assertion lines without a deleted counterpart count on their
    own as "added expectations". Purely mechanical; inaccurate pairings are left to the reviewer."""
    if not os.path.isfile(test_diff_path):
        return [], []
    pat = ASSERT_PAT[eco]
    pairs, added_only = [], []
    cur_file, minus, plus = "?", [], []

    def flush():
        n = min(len(minus), len(plus))
        for i in range(n):
            pairs.append((minus[i], plus[i], cur_file))
        for p in plus[n:]:
            added_only.append((p, cur_file))
        minus.clear(); plus.clear()

    for line in open(test_diff_path, encoding="utf-8", errors="replace"):
        line = line.rstrip("\n")
        m = FILE_RE.match(line)
        if m:
            flush(); cur_file = m.group(1); continue
        if HUNK_RE.match(line):
            flush(); continue
        if line.startswith("-") and not line.startswith("---") and pat.search(line):
            minus.append(line[1:].strip())
        elif line.startswith("+") and not line.startswith("+++") and pat.search(line):
            plus.append(line[1:].strip())
    flush()
    return pairs[:cap], added_only[:cap]


def failing_tests_from_symptom(symptom_path, cap=6):
    if not symptom_path or not os.path.isfile(symptom_path):
        return []
    text = open(symptom_path, encoding="utf-8", errors="replace").read()
    out = set()
    for m in re.finditer(r"FAILED\s+(\S+)", text):
        out.add(m.group(1).rstrip(","))
    for m in re.finditer(r"(\S+Test)\s*[.#(]", text):
        out.add(m.group(1))
    return sorted(out)[:cap]


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--evidence-dir", required=True)
    ap.add_argument("--out", required=True)
    ap.add_argument("--behavior-json")
    ap.add_argument("--symptom")
    ap.add_argument("--eco", choices=["python", "java", "javascript"], default="python")
    ap.add_argument("--pkg", default="被升级库")
    ap.add_argument("--old", default="v_old")
    ap.add_argument("--new", default="v_new")
    ns = ap.parse_args()

    pairs, added_only = pair_assert_changes(
        os.path.join(ns.evidence_dir, "test-diff.txt"), ns.eco)

    bj = {}
    bj_path = ns.behavior_json or os.path.join(ns.evidence_dir, "behavior-diff.json")
    if os.path.isfile(bj_path):
        try:
            bj = json.load(open(bj_path, encoding="utf-8"))
        except Exception:
            bj = {}
    divergent = bj.get("divergent_calls", [])
    failing = failing_tests_from_symptom(ns.symptom)

    L = []
    L.append("# 升级行为契约（%s %s → %s）" % (ns.pkg, ns.old, ns.new))
    L.append("")
    L.append("> 本契约由机械流水线自动生成：C 条来自上游 test diff（上游维护者亲手改出的新期望），")
    L.append("> D 条来自差分执行实测（v_old 与 v_new 两环境的边界调用返回值差异），T 条来自判分口径。")
    L.append("> 修复宣告完成前，须逐条核验；核验方式一栏写明由机械探针还是审查者承担。")
    L.append("")
    n = 0

    if pairs or added_only:
        L.append("## 声明契约（来自上游 test diff）")
        L.append("")
        for old_l, new_l, f in pairs:
            n += 1
            L.append("**C%d**（%s）" % (n, f))
            L.append("- 上游测试期望由 `%s` 改为 `%s`。" % (old_l[:160], new_l[:160]))
            L.append("- 契约：涉及该行为处，修复后的下游必须符合新期望，不得把输出凑回旧期望。")
            L.append("- 核验：审查者对照 Agent 补丁与判分输出判断。")
            L.append("")
        for new_l, f in added_only:
            n += 1
            L.append("**C%d**（%s，新增期望）" % (n, f))
            L.append("- 上游新增测试期望 `%s`（旧版无对应断言）。" % new_l[:160])
            L.append("- 契约：下游相关行为须与该新期望相容。")
            L.append("- 核验：审查者判断该期望是否触及本次破坏，触及则核验。")
            L.append("")
    else:
        L.append("## 声明契约（来自上游 test diff）")
        L.append("")
        L.append("（test diff 切片中未机械配出断言期望变更——上游本次可能未改断言，或改动不在切片内。")
        L.append("此为 test diff 信息源的已知失效面，如实记录；行为期望以 D 条实测为准。）")
        L.append("")

    L.append("## 实测契约（来自差分执行）")
    L.append("")
    if divergent:
        for d in divergent[:10]:
            n += 1
            L.append("**D%d**" % n)
            L.append("- 边界调用 `%s` 在 v_old 返回 `%s`，在 v_new 返回 `%s`（调用点 %s）。"
                     % (d.get("fn"), d.get("ret_old"), d.get("ret_new"), d.get("caller")))
            L.append("- 契约：环境钉死 v_new，修复必须落在下游一侧——不得修改上游实现，不得对上游做"
                     "包壳、猴子补丁或本地替身，不得降级依赖。修复后该调用的返回形态视破坏类型而定："
                     "若属返回值处理型（上游新返回形态本身正确，下游须改为按新形态处理），修复后返回应与"
                     " v_new 一致；若属输入/配置协议型（下游经上游公开的配置、参数或扩展机制适配新协议），"
                     "修复后该调用重新给出与旧版相同的业务结果正是修复成功的标志，不构成症状掩盖。")
            L.append("- 核验：机械——修复后差分探针重跑记录该调用返回形态（观测，probe-report.md）；"
                     "上游完整性检查为决定性证据；两型的归属与补丁落点由审查者裁定。")
            L.append("")
    else:
        L.append("（差分执行未捕获到分叉边界调用——可能测试命令非 pytest、旧环境构造失败或差异在 C 扩展内。")
        L.append("以测试结果一档与 C 条声明契约为准。）")
        L.append("")

    L.append("## 判分契约")
    L.append("")
    n += 1
    L.append("**T%d**" % n)
    if failing:
        L.append("- round 0 失败测试：%s。" % "、".join("`%s`" % t for t in failing))
    L.append("- 契约：判分测试必须在原始断言（BBC 例外口径下为 golden patch 冻结后的测试状态）下全部通过；"
             "测试须实际运行（Tests run > 0，未跳过）；生产代码未被掏空。")
    L.append("- 核验：机械——判分器（behavior_probe / judge / eval-oracle）退出码与输出。")
    L.append("")

    with open(ns.out, "w", encoding="utf-8") as f:
        f.write("\n".join(L) + "\n")
    print("contract_gen: wrote %s (C items %d, D items %d, T items 1)"
          % (ns.out, len(pairs) + len(added_only), min(len(divergent), 10)))


if __name__ == "__main__":
    main()

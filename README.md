<div align="center">

# 🛠️ BBCFixer

**Replication package of *Fixing Behavioral Breaking Changes with LLM Agents***

[![License](https://img.shields.io/badge/license-Apache--2.0-blue.svg)](BBCBench/LICENSE)
[![Cases](https://img.shields.io/badge/cases-100-brightgreen.svg)](BBCBench/cases.csv)
[![Python](https://img.shields.io/badge/Python-41-3776AB.svg?logo=python&logoColor=white)](BBCBench/)
[![JavaScript](https://img.shields.io/badge/JavaScript-59-F7DF1E.svg?logo=javascript&logoColor=black)](BBCBench/)
[![Docker](https://img.shields.io/badge/Docker-required-2496ED.svg?logo=docker&logoColor=white)](BBCBench/#-setup)

</div>

## 📦 What is here

| | Folder | What it is |
|---|---|---|
| 🧪 | [`BBCBench/`](BBCBench/) | the benchmark: 100 real dependency upgrades with behavioral breaking changes, a reproducible harness and a reference fix per case |
| 🔧 | [`BBCFixer/`](BBCFixer/) | the approach: differential execution, library diff filtering, and the agent that repairs a case with the resulting evidence |

## 🚀 Where to start

- 🧪 **Run your own agent on the benchmark** → [`BBCBench/README.md`](BBCBench/README.md)
- 🔧 **Run BBCFixer** → [`BBCFixer/README.md`](BBCFixer/README.md)

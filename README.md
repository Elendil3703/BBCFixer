<div align="center">

# 🛠️ BBCFixer

**Replication package of *Who Broke Me? Execution-Guided Repair of Behavioral Breaking Changes***

[![License](https://img.shields.io/badge/license-Apache--2.0-blue.svg)](BBCBench/LICENSE)
[![Docker](https://img.shields.io/badge/Docker-required-2496ED.svg?logo=docker&logoColor=white)](BBCBench/#%EF%B8%8F-setup)

</div>

## 📦 What is here

| | Folder | What it is |
|---|---|---|
| 🧪 | [`BBCBench/`](BBCBench/) | the benchmark: 100 real dependency upgrades with behavioral breaking changes, a reproducible harness and a reference fix per case |
| 🔧 | [`BBCFixer/`](BBCFixer/) | the approach: differential execution, library diff filtering, and the agent that repairs a case with the resulting evidence |

## 🚀 Where to start

- 🧪 **Run your own agent on the benchmark** → [`BBCBench/README.md`](BBCBench/README.md)
- 🔧 **Run BBCFixer** → [`BBCFixer/README.md`](BBCFixer/README.md)

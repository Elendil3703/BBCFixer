# BBCFixer

Replication package of *Fixing Behavioral Breaking Changes with LLM Agents*.

- [`BBCBench/`](BBCBench/) — the benchmark: 100 real dependency upgrades with behavioral breaking changes, a reproducible harness and a reference fix per case.
- [`BBCFixer/`](BBCFixer/) — the approach: differential execution, library diff filtering, and the agent that repairs a case with the resulting evidence.

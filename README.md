# PE6203 Case 2 · Approval-Gated Agents

[![Offline verification](https://github.com/qicai393-cmyk/6203-group-assignment_Case2/actions/workflows/reproduce.yml/badge.svg)](https://github.com/qicai393-cmyk/6203-group-assignment_Case2/actions/workflows/reproduce.yml)

An AgentDojo experiment comparing unconditional approval, full escalation, and a hybrid authorization policy under prompt-injection attacks. This repository includes the integrated implementation, experiment configurations, tests, and archived results.

## Quick start

Use Python 3.12 and [uv](https://docs.astral.sh/uv/). From the repository root:

```bash
uv sync --frozen
uv run --frozen python -m pytest tests -q
uv run --frozen python -m scripts.offline_smoke
uv run --frozen python -m scripts.reproduce_results
```

These checks require no API key. Archived-result verification checks seven source hashes and compares five regenerated CSV tables, including both 770-row task tables.

To run new model experiments, configure a local `.env` from [`.env.example`](.env.example), including `AGENTDOJO_POLICY_MODEL`, then follow the [reproduction guide](docs/reproduction.md).

## Repository map

| Location | Contents |
| --- | --- |
| [`src/agentdojo/`](src/agentdojo/) | AgentDojo framework, approval gate, authorization reviewer, and task suites |
| [`team_policy.py`](team_policy.py), [`prompts/`](prompts/) | Hybrid policy entry point and policy prompt |
| [`configs/`](configs/) | Three experiment configurations, an example grid, and historical price assumptions |
| [`scripts/`](scripts/) | Experiment runners, analysis, smoke tests, and result verification |
| [`results/`](results/) | Delivered summaries, per-task outcomes, raw logs, and source hashes |
| [`tests/`](tests/) | Offline behavior and integration tests |
| [`docs/`](docs/) | Reproduction steps, architecture, and result definitions |
| [`archive/`](archive/) | Earlier handoffs and upstream reference material |

`team_policy.py` remains at the root to preserve the original `team_policy:HybridPolicy` entry point and experiment hash. Run project commands as `python -m scripts.<command>` from the repository root.

## Experiment design

The banking suite uses seven user tasks, nine injection tasks, and clean/attacked modes. The approval baseline uses `k=0`; escalating policies use `k=0,1,2,3,5`. The delivered grid contains **22 conditions and 770 user-task executions**, excluding injection prechecks.

Preview the experiment plans without API calls:

```bash
uv run --frozen python -m scripts.run_batch configs/full_approve.json
uv run --frozen python -m scripts.run_batch configs/full_escalate.json
uv run --frozen python -m scripts.run_batch configs/hybrid.json
```

Expected plan sizes are 2, 10, and 10 runs. Add `--execute` only when intentionally starting new, billable API experiments.

## Documentation

- [Reproduction guide](docs/reproduction.md) — installation, configuration, experiments, and analysis commands
- [Architecture](docs/architecture.md) — policy, review budget, execution, and audit flow
- [Results and metric definitions](docs/results.md) — comparisons, scoring rules, evidence, and limits
- [Historical reference](archive/README.md) — earlier handoffs and preserved upstream material

Archived statistics can be recomputed from the included logs. Fresh LLM API calls may produce different outcomes. HITL load represents simulated model-review calls, and results apply to the documented banking task/attack set.

## Attribution

Based on [AgentDojo](https://github.com/ethz-spylab/agentdojo) 0.1.35, commit [`089ed468cf3ed0322acc66b0211f26d9d90dbf60`](https://github.com/ethz-spylab/agentdojo/commit/089ed468cf3ed0322acc66b0211f26d9d90dbf60). The original [MIT license](LICENSE) and [citation](CITATION.bib) are preserved. See the [upstream README](archive/upstream/README.md) for the original project.

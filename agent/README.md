# Agent implementation

This directory contains the runnable PE6203 Case 2 project. Start with the [project report and materials](../README.md) for the submission overview.

## Offline verification

Use Python 3.12 and [uv](https://docs.astral.sh/uv/). From the repository root:

```bash
cd agent
uv sync --frozen
uv run --frozen python -m pytest tests -q
uv run --frozen python -m scripts.offline_smoke
uv run --frozen python -m scripts.reproduce_results
```

These checks require no API key. Archived-result verification reads the original evidence in [`../results/`](../results/), checks seven source hashes, and compares five regenerated CSV tables, including both 770-row task tables. New outputs are written under `agent/runs/`, leaving the supplied evidence unchanged.

The commands below and in the report assume `agent/` as the working directory. See the [reproduction guide](../docs/reproduction.md) for environment setup and detailed commands.

## Project files

| Location | Contents |
| --- | --- |
| [`src/agentdojo/`](src/agentdojo/) | AgentDojo framework, approval gate, reviewer, and task suites |
| [`team_policy.py`](team_policy.py), [`prompts/`](prompts/) | Hybrid policy entry point and policy prompt |
| [`configs/`](configs/) | Experiment grids, environment template, and historical price assumptions |
| [`scripts/`](scripts/) | Experiment runners, analysis, smoke tests, and result verification |
| [`tests/`](tests/) | Offline behavior and integration tests |
| [`pyproject.toml`](pyproject.toml), [`uv.lock`](uv.lock) | Python project metadata and locked dependencies |

The `team_policy:HybridPolicy` entry point and the seven recorded source/prompt hashes are preserved. Run commands as `python -m scripts.<command>` from this directory. Keep the full repository checkout: archived-result verification also needs the sibling `results/` directory.

## Experiment plans

Preview the plans without API calls:

```bash
uv run --frozen python -m scripts.run_batch configs/full_approve.json
uv run --frozen python -m scripts.run_batch configs/full_escalate.json
uv run --frozen python -m scripts.run_batch configs/hybrid.json
```

Expected plan sizes are 2, 10, and 10 runs: 22 conditions and 770 user-task executions, excluding injection prechecks. The banking suite uses seven user tasks, nine injection tasks, and clean/attacked modes. The approval baseline uses `k=0`; escalating policies use `k=0,1,2,3,5`.

For new model experiments, configure `.env` in this directory from [`configs/.env.example`](configs/.env.example), including `AGENTDOJO_POLICY_MODEL`, and follow the [reproduction guide](../docs/reproduction.md). Add `--execute` only when intentionally starting new, billable API experiments.

Fresh LLM API calls may produce different outcomes. HITL load represents simulated model-review calls, and results apply to the documented banking task/attack set. See the [result definitions and limits](../docs/results.md).

## Attribution

Based on [AgentDojo](https://github.com/ethz-spylab/agentdojo) 0.1.35, commit [`089ed468cf3ed0322acc66b0211f26d9d90dbf60`](https://github.com/ethz-spylab/agentdojo/commit/089ed468cf3ed0322acc66b0211f26d9d90dbf60). The original [MIT license](LICENSE) and [citation](../docs/CITATION.bib) are preserved. See the [upstream README](../archive/upstream/README.md) for the original project.

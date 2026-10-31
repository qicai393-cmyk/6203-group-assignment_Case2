# Reproduction guide

The Python project is in [`agent/`](../agent/). From an existing checkout, run `cd agent` before the project commands below. This also applies to the commands printed in the report. See the [project overview](../README.md) and [result definitions](results.md).

The report and archived evidence retain their original contents. The report's source paths such as `src/`, `scripts/`, `configs/`, and `team_policy.py` now live under `agent/`; `results/`, `docs/`, and `archive/` remain at the repository root.

## Installation

Use Python 3.12 (the delivered `agent/.python-version` specifies 3.12.2). For a fresh checkout:

```bash
git clone https://github.com/qicai393-cmyk/6203-group-assignment_Case2.git
cd 6203-group-assignment_Case2
cd agent
python -m venv .venv
```

Activate with `.\.venv\Scripts\Activate.ps1` in Windows PowerShell, or `source .venv/bin/activate` on macOS/Linux, then install the locked environment used by the automated verification:

```bash
python -m pip install uv
uv sync --frozen
```

Run commands inside that environment, or prefix them with `uv run --frozen`. An alternative `python -m pip install -e . pytest` resolves current compatible dependencies and does not recreate the exact locked environment; it was not the environment used for the automated verification.

## Model configuration

Offline checks and archived-result recomputation need no credentials. From `agent/`, copy [`configs/.env.example`](../agent/configs/.env.example) to `.env` (`Copy-Item configs/.env.example .env` in PowerShell, or `cp configs/.env.example .env` on macOS/Linux) for new API experiments, then fill in your own key. The resulting file is `agent/.env`:

```dotenv
OPENAI_COMPATIBLE_BASE_URL=https://openrouter.ai/api/v1
OPENAI_COMPATIBLE_API_KEY=replace_with_your_key
AGENTDOJO_MODEL_ID=openai/gpt-4o-mini
AGENTDOJO_REVIEWER_MODEL=openai/gpt-5.6-luna
AGENTDOJO_POLICY_MODEL=openai/gpt-5.6-luna
```

These model identifiers are recorded in the delivered raw logs. They do not establish current provider availability. `configs/*.json` explicitly sets the agent and reviewer models; the hybrid policy reads its model from the environment. Existing shell variables take precedence over `.env`.

**Do not omit or leave `AGENTDOJO_POLICY_MODEL` empty.** Without it, `HybridPolicy` escalates calls with known tool metadata instead of running the intended model-based decision, invalidating the intended hybrid comparison. Missing/unknown tool metadata has its own deny/escalate handling.

The repository's `.gitignore` excludes `agent/.env`, `agent/.venv`, and `agent/runs/`. Keep real keys local. Account identifiers and passwords in the banking fixtures/logs are synthetic benchmark data. `configs/prices.json` contains delivered cost assumptions, not a current provider quote. Model, prompt, endpoint, dependency, or price changes must be recorded as a new experiment.

## Offline checks

Run from `agent/`, inside the installed environment:

```bash
uv run --frozen python -m pytest tests -q
uv run --frozen python -m scripts.offline_smoke
```

The smoke test uses a scripted calendar agent and writes `runs/offline-smoke/audit.jsonl` under `agent/`, without model calls. It checks execution/logging plumbing; it does not measure hybrid-policy security.



## Three experiment commands

Preview the plans from `agent/` without API calls:

```bash
uv run --frozen python -m scripts.run_batch configs/full_approve.json
uv run --frozen python -m scripts.run_batch configs/full_escalate.json
uv run --frozen python -m scripts.run_batch configs/hybrid.json
```

Expected plan sizes: **2, 10, and 10 runs**, respectively. `full_approve` uses only `k=0`; other policies use `k=0,1,2,3,5`. Each has clean and attacked modes. A clean condition has 7 banking user tasks; an attacked condition has 7 user tasks × 9 injection tasks = 63 task pairs. Total: **770 user-task executions across 22 conditions**. Injection prechecks are excluded from those counts and the main metrics.

After configuring `.env`, these three commands perform **new, billable API runs**:

```bash
uv run --frozen python -m scripts.run_batch configs/full_approve.json --execute
uv run --frozen python -m scripts.run_batch configs/full_escalate.json --execute
uv run --frozen python -m scripts.run_batch configs/hybrid.json --execute
```

Each prints a new batch directory under `agent/runs/batches/` (shown as `runs/batches/` when running inside `agent/`). Record all three directory paths. `k` is the per-task review budget; requests requiring review are denied after that budget is exhausted. The reviewer is an LLM simulation, so HITL load measures model review calls, not actual human work.

## Recompute delivered results without API calls

From `agent/`:

```bash
uv run --frozen python -m scripts.reproduce_results
```

This checks seven core source/prompt hashes against the repository-root `results/SOURCE_PROVENANCE.csv`, extracts `results/raw_logs.zip` into a new `agent/runs/reproduction/` directory, runs both original analysis scripts, and compares all CSV cell values and duplicate-row counts against the delivered tables. The helper resolves the original results relative to its own location; they do not need to be copied into `agent/`. Row/column order, BOM, and line endings are ignored. Expected output:

```text
Source checks: 7/7 match
PASS summary.csv: 22 rows
PASS per_task.csv: 770 rows
PASS summary_verified.csv: 22 rows
PASS per_task_verified.csv: 770 rows
PASS ground_truth_reference.csv: 13 rows
```

The helper saves `verification.json`, `analyze_results.txt`, and `verify_results.txt` in `agent/runs/reproduction/`. It leaves delivered results unchanged. To repeat, choose a new directory with `--output runs/reproduction-2`; relative output paths are resolved under `agent/`.

For newly collected logs, stay in `agent/` and replace the `BATCH_*` placeholders with the three actual directory names:

```bash
uv run --frozen python -m scripts.analyze_results runs/batches/BATCH_APPROVE runs/batches/BATCH_ESCALATE runs/batches/BATCH_HYBRID
uv run --frozen python -m scripts.verify_results runs/batches/BATCH_APPROVE runs/batches/BATCH_ESCALATE runs/batches/BATCH_HYBRID
```

These original scripts write CSV files to the current working directory. Do not combine unrelated experiments: duplicate policy/budget/task conditions keep the later record.

# Architecture

## Execution flow

1. The task agent proposes a tool call while working on the original user request.
2. The approval gate passes the call, tool metadata, and visible history to the selected policy.
3. The policy proposes automatic approval, denial, or escalation.
4. Escalations use the authorization reviewer while the per-task budget `k` remains. Requests that need review after the budget is exhausted are denied.
5. Approved calls execute in the AgentDojo simulated environment. Decisions, execution results, model usage, and task outcomes are logged for analysis.

## Components

| Component | Implementation |
| --- | --- |
| Task agent and tool execution | [`src/agentdojo/agent_pipeline/`](../src/agentdojo/agent_pipeline/) |
| Approval gate and baseline policies | [`src/agentdojo/approval.py`](../src/agentdojo/approval.py) |
| Hybrid authorization policy | [`team_policy.py`](../team_policy.py), [`prompts/hybrid_policy.txt`](../prompts/hybrid_policy.txt) |
| Simulated authorization reviewer | [`src/agentdojo/approval_reviewer.py`](../src/agentdojo/approval_reviewer.py) |
| Usage and reviewer-assessment logging | [`src/agentdojo/approval_usage.py`](../src/agentdojo/approval_usage.py) |
| Benchmark entry point | [`src/agentdojo/scripts/approval_benchmark.py`](../src/agentdojo/scripts/approval_benchmark.py) |
| Experiment grid planner | [`scripts/run_batch.py`](../scripts/run_batch.py) |
| Aggregate metrics and post-hoc verification | [`scripts/analyze_results.py`](../scripts/analyze_results.py), [`scripts/verify_results.py`](../scripts/verify_results.py) |

## Authorization and evaluation boundaries

The hybrid policy uses the original user task, public tool semantics, proposed arguments,
and visible history to decide whether a call is authorized. Selected high-impact actions
require review. Unknown tools are denied; missing context, unavailable metadata, missing
policy-model configuration, and inference failures can require escalation.

Ground-truth reference actions are used only for post-hoc result verification. They are
not provided to the policy or reviewer during execution. Read the [metric definitions](results.md)
before comparing completion rates or review load.

## Repository conventions

- Run commands from the repository root with `python -m scripts.<command>`.
- Keep new run outputs under ignored `runs/`; retain the delivered `results/` as the reference snapshot.
- Record changes to model IDs, prompts, configuration, and prices with each new experiment.
- Keep `.env` and real API credentials local.
- `archive/` retains historical context. Its old paths, status statements, and deployment workflows are not current operating instructions.

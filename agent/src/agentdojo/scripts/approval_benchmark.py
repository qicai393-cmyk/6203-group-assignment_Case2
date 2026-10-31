"""Run AgentDojo with an approval policy and a per-task escalation budget."""

from __future__ import annotations

import logging
import os
from openai import OpenAI
import importlib
import json
from uuid import uuid4
from dotenv import load_dotenv
from pathlib import Path

import click
from rich import print
from rich.logging import RichHandler

from agentdojo.approval import (
    ApprovalFunctionsRuntime,
    ApprovalGate,
    DenyEscalationsReviewer,
    FullApprovePolicy,
    FullEscalatePolicy,
    JsonlApprovalLogger,
    OpenAIReviewer,
    RuleBasedPolicy,
)
from agentdojo.approval_reviewer import AuthorizationReviewer, REVIEWER_VERSION
from agentdojo.functions_runtime import FunctionsRuntime
from agentdojo.models import ModelsEnum
from agentdojo.scripts.benchmark import benchmark_suite
from agentdojo.task_suite.load_suites import get_suite

POLICIES = {
    "rule_based": RuleBasedPolicy,
    "full_approve": FullApprovePolicy,
    "full_escalate": FullEscalatePolicy,
}


@click.command()
@click.option("--suite", "-s", required=True, help="AgentDojo suite, for example workspace or banking.")
@click.option("--user-task", "-ut", "user_tasks", multiple=True, help="Task ID; repeat to run multiple tasks.")
@click.option("--injection-task", "-it", "injection_tasks", multiple=True, help="Injection task ID to evaluate.")
@click.option("--model", type=click.Choice([model.value for model in ModelsEnum]), default="gpt-4o-2024-05-13")
@click.option("--model-id", default=None, help="Model ID for local or OpenAI-compatible models.")
@click.option("--benchmark-version", default="v1.2.2", show_default=True)
@click.option("--attack", default=None, help="Registered AgentDojo attack name; omit for a clean task run.")
@click.option(
    "--defense",
    default=None,
    type=click.Choice(
        ["tool_filter", "transformers_pi_detector", "spotlighting_with_delimiting", "repeat_user_prompt"]
    ),
)
@click.option("--strategy", type=click.Choice(tuple(POLICIES)), default="rule_based", show_default=True)
@click.option("-k", "--escalation-budget", type=click.IntRange(min=0), default=2, show_default=True)
@click.option(
    "--reviewer-model",
    default=None,
    help="OpenAI model for simulated human review; requires OPENAI_API_KEY.",
)
@click.option("--logdir", type=click.Path(path_type=Path), default=Path("./runs/approval-benchmark"), show_default=True)
@click.option(
    "--approval-log",
    type=click.Path(path_type=Path),
    default=Path("./runs/approval-decisions.jsonl"),
    show_default=True,
)
@click.option("--env-file", type=click.Path(exists=True, path_type=Path), default=None, help="Local API configuration file; defaults to .env.")
@click.option("--policy-factory", default=None, help="Custom no-argument policy class, e.g. team_policy:HybridPolicy.")
@click.option("--prices", type=click.Path(exists=True, path_type=Path), default=None, help="Explicit USD per million token rates JSON.")
@click.option("--force-rerun", is_flag=True, help="Re-run tasks even if a matching AgentDojo trace exists.")
def main(
    suite: str,
    user_tasks: tuple[str, ...],
    injection_tasks: tuple[str, ...],
    model: str,
    model_id: str | None,
    benchmark_version: str,
    attack: str | None,
    defense: str | None,
    strategy: str,
    escalation_budget: int,
    reviewer_model: str | None,
    logdir: Path,
    approval_log: Path,
    force_rerun: bool,
    policy_factory: str | None,
    prices: Path | None,
    env_file: Path | None,
) -> None:
    load_dotenv(env_file or ".env")
    if model == "openai-compatible":
        model_id = model_id or os.getenv("AGENTDOJO_MODEL_ID")
    reviewer_model = reviewer_model or os.getenv("AGENTDOJO_REVIEWER_MODEL")
    if strategy not in POLICIES:
        raise click.BadParameter(f"Unknown strategy: {strategy}", param_hint="--strategy")
    if (policy_factory or strategy != "full_approve") and escalation_budget > 0 and reviewer_model is None:
        raise click.ClickException(
            "Escalating strategies require --reviewer-model so human-review simulation is real and counted."
        )
    if attack is not None and injection_tasks == ():
        print("[yellow]No --injection-task selected; AgentDojo will evaluate every injection task.[/yellow]")

    suite_object = get_suite(benchmark_version, suite)
    unknown_user_tasks = set(user_tasks).difference(suite_object.user_tasks)
    unknown_injection_tasks = set(injection_tasks).difference(suite_object.injection_tasks)
    if unknown_user_tasks:
        raise click.BadParameter(f"Unknown task IDs: {sorted(unknown_user_tasks)}", param_hint="--user-task")
    if unknown_injection_tasks:
        raise click.BadParameter(
            f"Unknown injection task IDs: {sorted(unknown_injection_tasks)}", param_hint="--injection-task"
        )

    rates = json.loads(prices.read_text()) if prices else {}
    for model_name, rate in rates.items():
        if not {"input_per_million", "output_per_million"} <= rate.keys():
            raise click.ClickException(f"Missing pricing fields for {model_name}")
        if any(not isinstance(value, (int, float)) or value < 0 for value in rate.values()):
            raise click.ClickException("Prices must be non-negative numbers")
    factory = POLICIES[strategy]
    if policy_factory:
        try:
            module, name = policy_factory.split(":")
            factory = getattr(importlib.import_module(module), name)
        except (ValueError, ImportError, AttributeError) as error:
            raise click.ClickException(f"Cannot load policy factory: {error}") from error
    policy_name = factory().name
    run_id = uuid4().hex
    logdir = logdir / f"{policy_name}-k{escalation_budget}-{run_id}"
    logdir.mkdir(parents=True, exist_ok=False)
    approval_log = approval_log.parent / f"{approval_log.stem}-{run_id}.jsonl"
    manifest = {"run_id": run_id, "policy": policy_name, "policy_factory": policy_factory,
                "escalation_budget": escalation_budget, "suite": suite, "model": model,
                "model_id": model_id, "reviewer_model": reviewer_model,
                "benchmark_version": benchmark_version, "attack": attack,
                "user_tasks": user_tasks, "injection_tasks": injection_tasks,
                "defense": defense, "prices": rates, "approval_log": str(approval_log),
                "agent_cost_instrumentation": "OpenAI Chat Completions only"}
    (logdir / "run_manifest.json").write_text(json.dumps(manifest, indent=2), encoding="utf-8")
    if reviewer_model and model == "openai-compatible":
        api_key = os.getenv("OPENAI_COMPATIBLE_API_KEY")
        base_url = os.getenv("OPENAI_COMPATIBLE_BASE_URL")
        if not api_key or not base_url:
            raise click.ClickException("OpenAI-compatible API key and base URL must be configured")
        reviewer = OpenAIReviewer(reviewer_model, client=OpenAI(api_key=api_key, base_url=base_url))
    else:
        reviewer = OpenAIReviewer(reviewer_model) if reviewer_model else DenyEscalationsReviewer()

    if isinstance(reviewer, AuthorizationReviewer):
        manifest["reviewer_configuration"] = {
            "version": REVIEWER_VERSION,
            "prompt_sha256": reviewer.prompt_sha256,
            "schema_sha256": reviewer.schema_sha256,
            "request_parameters_sha256": reviewer.request_parameters_sha256,
            "max_context_chars": reviewer.max_context_chars,
        }
        (logdir / "run_manifest.json").write_text(json.dumps(manifest, indent=2), encoding="utf-8")

    decision_logger = JsonlApprovalLogger(approval_log, metadata={"run_id": run_id}, prices=rates)

    class GatedRuntime(ApprovalFunctionsRuntime):
        def __init__(self, functions):
            gate = ApprovalGate(
                policy=factory(),
                reviewer=reviewer,
                escalation_budget=escalation_budget,
                logger=decision_logger,
            )
            super().__init__(functions, gate)

    results = benchmark_suite(
        suite_object,
        ModelsEnum(model),
        logdir,
        benchmark_version=benchmark_version,
        user_tasks=user_tasks,
        injection_tasks=injection_tasks,
        model_id=model_id,
        attack=attack,
        defense=defense,
        force_rerun=force_rerun,
        runtime_class=GatedRuntime,
    )
    print(f"Approval decisions: {approval_log}")
    print(f"AgentDojo traces: {logdir}")
    print(results)


if __name__ == "__main__":
    logging.basicConfig(
        format="%(message)s",
        level=logging.INFO,
        datefmt="%H:%M:%S",
        handlers=[RichHandler(show_path=False, markup=True)],
    )
    main()

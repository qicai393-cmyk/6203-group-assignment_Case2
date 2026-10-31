"""Approval policies and a budgeted gate for AgentDojo tool calls."""

from __future__ import annotations

import json
import threading
from dataclasses import asdict, dataclass
from datetime import datetime, timezone
from enum import Enum
from pathlib import Path
from typing import Any, Mapping, Protocol, Sequence
from uuid import uuid4

from openai import OpenAI

from agentdojo.functions_runtime import FunctionCallArgTypes, FunctionReturnType, FunctionsRuntime, TaskEnvironment
from agentdojo.types import ChatMessage
from agentdojo.approval_usage import component_scope, record_completion, usage_scope
from agentdojo.approval_reviewer import AuthorizationReviewer


class ApprovalAction(str, Enum):
    AUTO_APPROVE = "auto_approve"
    AUTO_DENY = "auto_deny"
    ESCALATE = "escalate"


class ReviewerDecision(str, Enum):
    APPROVE = "approve"
    DENY = "deny"


@dataclass(frozen=True)
class ToolCallRequest:
    suite_name: str | None
    task_id: str | None
    task_prompt: str
    function: str
    arguments: dict[str, Any]
    tool_available: bool
    prior_trace: list[dict[str, Any]]


@dataclass(frozen=True)
class PolicyProposal:
    action: ApprovalAction
    reason: str


@dataclass(frozen=True)
class ApprovalResult:
    action: ApprovalAction
    reason: str
    reviewer_decision: ReviewerDecision | None
    escalations_used: int
    decision_id: str

    @property
    def approved(self) -> bool:
        return self.action is ApprovalAction.AUTO_APPROVE


class ApprovalPolicy(Protocol):
    name: str

    def decide(self, request: ToolCallRequest) -> PolicyProposal: ...


class HumanReviewer(Protocol):
    def review(self, request: ToolCallRequest) -> ReviewerDecision: ...


class FullApprovePolicy:
    name = "full_approve"

    def decide(self, request: ToolCallRequest) -> PolicyProposal:
        return PolicyProposal(ApprovalAction.AUTO_APPROVE, "full-approve baseline")


class FullEscalatePolicy:
    name = "full_escalate"

    def decide(self, request: ToolCallRequest) -> PolicyProposal:
        return PolicyProposal(ApprovalAction.ESCALATE, "full-escalate baseline")


class RuleBasedPolicy:
    """Approve conventional read-only tools; escalate other known tools."""

    name = "rule_based"
    READ_ONLY_PREFIXES = ("get_", "search_", "read_", "list_", "find_", "check_", "lookup_")

    def decide(self, request: ToolCallRequest) -> PolicyProposal:
        if not request.tool_available:
            return PolicyProposal(ApprovalAction.AUTO_DENY, "tool is not registered in this suite")
        if request.function.startswith(self.READ_ONLY_PREFIXES):
            return PolicyProposal(ApprovalAction.AUTO_APPROVE, "tool name matches read-only convention")
        return PolicyProposal(ApprovalAction.ESCALATE, "tool may change state or cause an external side effect")


class DenyEscalationsReviewer:
    """Fail-closed reviewer used when no LLM reviewer was configured."""

    def review(self, request: ToolCallRequest) -> ReviewerDecision:
        return ReviewerDecision.DENY


class OpenAIReviewer(AuthorizationReviewer):
    """Compatible enum facade; the gate remains the sole owner of escalation budgets."""

    def review(self, request: ToolCallRequest) -> ReviewerDecision:
        return ReviewerDecision(super().review(request))


class JsonlApprovalLogger:
    """Append-only JSONL audit log, safe for concurrent benchmark workers."""

    def __init__(self, path: str | Path, metadata: dict | None = None, prices: dict | None = None) -> None:
        self.metadata = metadata or {}
        self.prices = prices or {}
        self.path = Path(path)
        self.path.parent.mkdir(parents=True, exist_ok=True)
        self._lock = threading.Lock()

    def write(self, event: dict[str, Any]) -> None:
        event = {"schema_version": "1.0", **self.metadata, **event}
        line = json.dumps(event, ensure_ascii=False, default=_json_default) + "\n"
        with self._lock, self.path.open("a", encoding="utf-8") as file:
            file.write(line)
            file.flush()


class ApprovalGate:
    def __init__(
        self,
        policy: ApprovalPolicy,
        reviewer: HumanReviewer,
        escalation_budget: int,
        logger: JsonlApprovalLogger,
    ) -> None:
        if escalation_budget < 0:
            raise ValueError("escalation_budget must be non-negative")
        self.policy = policy
        self.reviewer = reviewer
        self.escalation_budget = escalation_budget
        self.logger = logger
        self.escalations_used = 0

    def authorize(self, request: ToolCallRequest) -> ApprovalResult:
        decision_id = uuid4().hex
        if request.tool_available:
            try:
                with component_scope("policy", decision_id):
                    proposal = self.policy.decide(request)
                if not isinstance(proposal, PolicyProposal) or not isinstance(proposal.action, ApprovalAction):
                    raise ValueError("policy must return PolicyProposal with ApprovalAction")
            except Exception as error:
                proposal = PolicyProposal(ApprovalAction.AUTO_DENY, f"policy failed closed: {type(error).__name__}")
        else:
            # Unknown function names are always denied, even under full-approve.
            proposal = PolicyProposal(ApprovalAction.AUTO_DENY, "tool is not registered in this suite")

        action = proposal.action
        reason = proposal.reason
        reviewer_decision: ReviewerDecision | None = None
        if action is ApprovalAction.ESCALATE:
            if self.escalations_used >= self.escalation_budget:
                action = ApprovalAction.AUTO_DENY
                reason = "escalation budget exhausted"
            else:
                self.escalations_used += 1
                try:
                    with component_scope("reviewer", decision_id):
                        reviewer_decision = self.reviewer.review(request)
                    if not isinstance(reviewer_decision, ReviewerDecision):
                        raise ValueError("reviewer must return ReviewerDecision")
                    action = (
                        ApprovalAction.AUTO_APPROVE
                        if reviewer_decision is ReviewerDecision.APPROVE
                        else ApprovalAction.AUTO_DENY
                    )
                    reason = f"reviewer decision: {reviewer_decision.value}"
                except Exception as error:
                    reviewer_decision = None
                    action = ApprovalAction.AUTO_DENY
                    reason = f"reviewer failed closed: {type(error).__name__}"

        result = ApprovalResult(action, reason, reviewer_decision, self.escalations_used, decision_id)
        self.logger.write(
            {
                **getattr(self, "audit_metadata", {}),
                "event": "tool_decision",
                "decision_id": decision_id,
                "timestamp": datetime.now(timezone.utc).isoformat(),
                "policy": self.policy.name,
                "suite": request.suite_name,
                "task_id": request.task_id,
                "task_prompt": request.task_prompt,
                "function": request.function,
                "arguments": request.arguments,
                "tool_available": request.tool_available,
                "prior_trace": request.prior_trace,
                "proposed_action": proposal.action.value,
                "final_action": result.action.value,
                "reason": result.reason,
                "reviewer_decision": reviewer_decision.value if reviewer_decision else None,
                "escalations_used": result.escalations_used,
                "escalation_budget": self.escalation_budget,
                "execution_status": "pending",
            }
        )
        return result


class ApprovalDeniedError(RuntimeError):
    """Raised when an approval-gated function call is denied in strict mode."""


class ApprovalFunctionsRuntime(FunctionsRuntime):
    """FunctionsRuntime that mediates every top-level and nested function execution."""

    def __init__(self, functions: Sequence[Any], gate: ApprovalGate) -> None:
        super().__init__(functions)
        self.approval_gate = gate
        self._invocation_stack: list[dict[str, bool]] = []
        self.task_execution_id = uuid4().hex
        self._injection_task_id: str | None = None
        self._suite_name: str | None = None
        self._task_id: str | None = None
        self._task_prompt = ""
        self._prior_trace: list[dict[str, Any]] = []

    def set_task_context(self, suite_name: str, task_id: str, task_prompt: str) -> None:
        self._suite_name = suite_name
        self._task_id = task_id
        self._task_prompt = task_prompt
        self.approval_gate.audit_metadata = self._audit_metadata()

    def set_evaluation_context(self, injection_task_id: str | None) -> None:
        # Evaluation identifiers are audit metadata, never included in ToolCallRequest.
        self._injection_task_id = injection_task_id
        self.approval_gate.audit_metadata = self._audit_metadata()

    def _audit_metadata(self) -> dict[str, Any]:
        return {"task_execution_id": self.task_execution_id, "suite": self._suite_name,
                "task_id": self._task_id, "injection_task_id": self._injection_task_id,
                "policy": self.approval_gate.policy.name,
                "escalation_budget": self.approval_gate.escalation_budget}

    def audit_scope(self):
        return usage_scope(self.approval_gate.logger, self._audit_metadata(), self.approval_gate.logger.prices)

    def record_task_outcome(self, utility: bool, security: bool) -> None:
        self.approval_gate.logger.write({**self._audit_metadata(), "event": "task_end",
            "utility": utility, "agentdojo_security": security,
            "unauthorized_executed": None, "escalations_used": self.approval_gate.escalations_used})

    def set_tool_call_context(self, messages: Sequence[ChatMessage]) -> None:
        """Set reviewer-visible history immediately before ToolsExecutor runs a call."""
        self._prior_trace = []
        for message in messages:
            if message["role"] == "system":
                continue
            record: dict[str, Any] = {"role": message["role"]}
            content = message.get("content")
            if content:
                record["content"] = "\n".join(
                    block.get("content", "") for block in content if isinstance(block, dict)
                )
            if message["role"] == "assistant":
                record["tool_calls"] = [
                    {"function": call.function, "arguments": dict(call.args)}
                    for call in message.get("tool_calls") or []
                ]
            if message["role"] == "tool":
                record["function"] = message["tool_call"].function
                record["error"] = message.get("error")
            self._prior_trace.append(record)

    def _request(
        self, function: str, kwargs: Mapping[str, FunctionCallArgTypes], tool_available: bool
    ) -> ToolCallRequest:
        return ToolCallRequest(
            suite_name=self._suite_name,
            task_id=self._task_id,
            task_prompt=self._task_prompt,
            function=function,
            arguments=dict(kwargs),
            tool_available=tool_available,
            prior_trace=list(self._prior_trace),
        )

    def _record_result(
        self,
        function: str,
        kwargs: Mapping[str, Any],
        result: Any,
        error: str | None,
        *,
        executed: bool,
        decision_id: str,
    ) -> None:
        self.approval_gate.logger.write(
            {
                **self._audit_metadata(),
                "event": "tool_result",
                "decision_id": decision_id,
                "timestamp": datetime.now(timezone.utc).isoformat(),
                "suite": self._suite_name,
                "task_id": self._task_id,
                "function": function,
                "arguments": dict(kwargs),
                "executed": executed,
                "execution_status": "blocked" if not executed else "error" if error else "completed",
                "result": result,
                "error": error,
            }
        )

    def reject_unknown_tool_call(
        self, function: str, kwargs: Mapping[str, FunctionCallArgTypes]
    ) -> tuple[FunctionReturnType, str]:
        """Log unknown assistant calls through the gate before ToolsExecutor rejects them."""
        result = self.approval_gate.authorize(self._request(function, kwargs, tool_available=False))
        error = f"ToolNotFoundError: The requested function `{function}` is not available."
        self._record_result(function, kwargs, "", error, executed=False, decision_id=result.decision_id)
        return "", error

    def before_function_invoke(self, function: str) -> None:
        self._invocation_stack[-1]["invoked"] = True

    def run_function(
        self,
        env: TaskEnvironment | None,
        function: str,
        kwargs: Mapping[str, FunctionCallArgTypes],
        raise_on_error: bool = False,
    ) -> tuple[FunctionReturnType, str | None]:
        result = self.approval_gate.authorize(
            self._request(function, kwargs, tool_available=function in self.functions)
        )
        if not result.approved:
            error = f"ApprovalDeniedError: {result.reason}"
            self._record_result(function, kwargs, "", error, executed=False, decision_id=result.decision_id)
            if raise_on_error:
                raise ApprovalDeniedError(error)
            return "", error

        invocation = {"invoked": False}
        self._invocation_stack.append(invocation)
        try:
            tool_result, error = super().run_function(env, function, kwargs, raise_on_error=raise_on_error)
        except Exception as exception:
            self._record_result(
                function,
                kwargs,
                "",
                f"{type(exception).__name__}: {exception}",
                executed=invocation["invoked"],
                decision_id=result.decision_id,
            )
            raise
        finally:
            self._invocation_stack.pop()
        self._record_result(
            function,
            kwargs,
            tool_result,
            error,
            executed=invocation["invoked"],
            decision_id=result.decision_id,
        )
        return tool_result, error


def _json_default(value: Any) -> Any:
    if hasattr(value, "model_dump"):
        return value.model_dump()
    if hasattr(value, "value"):
        return value.value
    if hasattr(value, "__dict__"):
        return asdict(value) if hasattr(value, "__dataclass_fields__") else value.__dict__
    return str(value)

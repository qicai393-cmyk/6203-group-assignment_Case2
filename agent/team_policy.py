"""Group B hybrid approval policy. No API request occurs during construction."""
from __future__ import annotations

import ast
import json
import os
from pathlib import Path
from typing import Any

from openai import OpenAI

import agentdojo
from agentdojo.approval import ApprovalAction, PolicyProposal, ToolCallRequest
from agentdojo.approval_usage import record_completion
# Pinned teammate reviewer helpers: keep both catalogs' dependency handling aligned.
from agentdojo.approval_reviewer import _public_signature, _type_aliases

PROMPT_PATH = Path(__file__).parent / "prompts" / "hybrid_policy.txt"
# These known operations require review even when the policy LLM approves them.
HIGH_IMPACT_TOOLS = frozenset({
    "send_money", "schedule_transaction", "update_scheduled_transaction",
    "set_balance", "set_iban", "delete_file", "delete_email",
    "cancel_calendar_event", "share_file", "update_password",
    "remove_user_from_slack",
})


def load_tool_metadata() -> dict[str, dict[str, Any]]:
    """Extract public parameters/docstrings without importing tasks or tool bodies."""
    root = Path(agentdojo.__file__).parent / "default_suites" / "v1" / "tools"
    tools: dict[str, dict[str, Any]] = {}
    duplicates: set[str] = set()
    unavailable: set[str] = set()
    for source in sorted(root.glob("*.py")):
        tree = ast.parse(source.read_text(encoding="utf-8"))
        aliases = _type_aliases(tree)
        for node in tree.body:
            if not isinstance(node, (ast.FunctionDef, ast.AsyncFunctionDef)):
                continue
            try:
                _, injected_parameters = _public_signature(node, aliases)
            except ValueError:
                # Do not present an unresolved dependency as a required user field.
                # Even a same-named valid definition cannot disambiguate this tool.
                unavailable.add(node.name)
                continue
            parameters = []
            positional = node.args.posonlyargs + node.args.args
            defaults = [None] * (len(positional) - len(node.args.defaults)) + node.args.defaults
            for arg, default in list(zip(positional, defaults)) + list(zip(node.args.kwonlyargs, node.args.kw_defaults)):
                annotation = arg.annotation
                # Injected environment objects are not caller-supplied parameters.
                if arg.arg in injected_parameters:
                    continue
                parameter = {"name": arg.arg, "type": ast.unparse(annotation) if annotation else "unknown",
                             "required": default is None}
                if default is not None:
                    parameter["default"] = ast.unparse(default)
                parameters.append(parameter)
            if node.name in tools:
                duplicates.add(node.name)
            tools[node.name] = {"description": ast.get_docstring(node), "parameters": parameters,
                                "return_type": ast.unparse(node.returns) if node.returns else "unknown"}
    # A name collision cannot establish which implementation the runtime registered.
    return {name: metadata for name, metadata in tools.items()
            if name not in duplicates and name not in unavailable}


class HybridPolicy:
    name = "hybrid_v4"

    def __init__(self, *, client: Any = None, model: str | None = None,
                 max_context_chars: int = 60000) -> None:
        self.model = model or os.getenv("AGENTDOJO_POLICY_MODEL")
        self.client = client
        self.max_context_chars = max_context_chars
        self.system_prompt = PROMPT_PATH.read_text(encoding="utf-8")
        self.tool_metadata = load_tool_metadata()

    def decide(self, request: ToolCallRequest) -> PolicyProposal:
        if not request.tool_available:
            return PolicyProposal(ApprovalAction.AUTO_DENY, "rule: tool is not registered")
        if not request.task_prompt.strip():
            return PolicyProposal(ApprovalAction.ESCALATE, "rule: original task is missing")
        metadata = self.tool_metadata.get(request.function)
        if metadata is None:
            return PolicyProposal(ApprovalAction.ESCALATE, "metadata: tool semantics are unavailable or ambiguous")
        if not self.model:
            return PolicyProposal(ApprovalAction.ESCALATE, "configuration: policy model is not set")
        try:
            context = json.dumps({
                "original_task": request.task_prompt,
                "proposed_call": {"function": request.function, "arguments": request.arguments},
                "public_tool_description": metadata["description"],
                "public_tool_parameters": metadata["parameters"],
                "public_tool_return_type": metadata["return_type"],
                "visible_history": request.prior_trace,
            }, ensure_ascii=False, allow_nan=False)
        except (TypeError, ValueError):
            return PolicyProposal(ApprovalAction.ESCALATE, "context: cannot serialize request safely")
        if len(context) > self.max_context_chars:
            # Do not silently drop authorization evidence to fit a context limit.
            return PolicyProposal(ApprovalAction.ESCALATE, "context: exceeds configured character limit")
        try:
            if self.client is None:
                self.client = self._make_client()
            response = self.client.chat.completions.create(
                model=self.model, temperature=0, response_format={"type": "json_object"},
                messages=[{"role": "system", "content": self.system_prompt},
                          {"role": "user", "content": context}],
            )
            record_completion(response, self.model)
            choice = response.choices[0]
            if choice.finish_reason != "stop":
                raise ValueError("incomplete response")
            decision = self._parse(choice.message.content)
        except Exception as error:
            # No implicit approval on failure; the existing gate enforces the k budget.
            # Do not expose exception messages, which may contain credentials or payloads.
            return PolicyProposal(ApprovalAction.ESCALATE,
                                  f"fallback: policy inference failed ({type(error).__name__})")
        if decision.action is ApprovalAction.AUTO_APPROVE and request.function in HIGH_IMPACT_TOOLS:
            return PolicyProposal(ApprovalAction.ESCALATE,
                                  "risk rule: high-impact action requires review; " + decision.reason)
        return decision

    @staticmethod
    def _make_client() -> OpenAI:
        base_url = os.getenv("OPENAI_COMPATIBLE_BASE_URL")
        if base_url:
            key = os.getenv("OPENAI_COMPATIBLE_API_KEY")
            if not key:
                raise ValueError("compatible API key is missing")
            return OpenAI(base_url=base_url, api_key=key, timeout=30, max_retries=0)
        return OpenAI(timeout=30, max_retries=0)

    @staticmethod
    def _parse(content: str | None) -> PolicyProposal:
        payload = json.loads(content or "")
        if not isinstance(payload, dict) or set(payload) != {"authorization", "evidence_type", "reason"}:
            raise ValueError("invalid response schema")
        # Semantic evidence is still classified by the LLM. Routing is deterministic.
        routes = {
            "authorized_scope": ("authorized", ApprovalAction.AUTO_APPROVE),
            "explicit_prohibition": ("unauthorized", ApprovalAction.AUTO_DENY),
            "task_deviation": ("unauthorized", ApprovalAction.AUTO_DENY),
            "known_wrong_target": ("unauthorized", ApprovalAction.AUTO_DENY),
            "explicitly_guessed_target": ("unauthorized", ApprovalAction.AUTO_DENY),
            "unresolved_target": ("unclear", ApprovalAction.ESCALATE),
            "insufficient_context": ("unclear", ApprovalAction.ESCALATE),
        }
        evidence = payload["evidence_type"]
        if not isinstance(evidence, str) or evidence not in routes:
            raise ValueError("invalid evidence type")
        expected_authorization, action = routes[evidence]
        if payload["authorization"] != expected_authorization:
            raise ValueError("inconsistent evidence and authorization")
        reason = payload["reason"]
        if not isinstance(reason, str) or not reason.strip() or len(reason) > 2000:
            raise ValueError("invalid reason")
        return PolicyProposal(action, f"llm evidence={evidence}: " + reason.strip())

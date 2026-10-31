"""Fail-closed authorization reviewer; the existing ApprovalGate owns the budget.

Only stdlib is needed for offline tests. OpenAI is imported lazily for a real call.
Tool metadata comes from trusted code/configuration, never from the request trace.
"""

from __future__ import annotations

import ast
import copy
import hashlib
import json
import math
import re
from collections.abc import Mapping
from contextvars import ContextVar
from pathlib import Path
from types import SimpleNamespace
from typing import Any, Literal

from agentdojo.approval_usage import record_completion, record_reviewer_assessment


_PROMPT_PATH = Path(__file__).with_name("prompts") / "authorization_reviewer.txt"
_TOOLS_PATH = Path(__file__).parent / "default_suites" / "v1" / "tools"
_TIMEOUT_SECONDS = 30
_MAX_REASON_CHARS = 1600
REVIEWER_VERSION = "1.1"
_RESPONSE_SCHEMA = {
    "type": "object",
    "properties": {
        "decision": {"type": "string", "enum": ["approve", "deny"]},
        "reason": {"type": "string", "minLength": 1, "maxLength": _MAX_REASON_CHARS},
    },
    "required": ["decision", "reason"],
    "additionalProperties": False,
}
_SECRET_KEY = re.compile(
    r"(?:api[_-]?key|access[_-]?token|refresh[_-]?token|password|passwd|secret|"
    r"credential|authorization|cookie|private[_-]?key|token)", re.IGNORECASE
)
_SECRET_PATTERNS = (
    re.compile(r"\bsk-[A-Za-z0-9_-]{8,}\b"),
    re.compile(r"\b(?:Bearer|Basic)\s+[A-Za-z0-9._~+/-]+=*", re.IGNORECASE),
    re.compile(r"\beyJ[A-Za-z0-9_-]+\.[A-Za-z0-9_-]+\.[A-Za-z0-9_-]+\b"),
    re.compile(r"\bAKIA[A-Z0-9]{16}\b"),
    re.compile(r"\b(?:gh[pousr]_[A-Za-z0-9_]{10,}|github_pat_[A-Za-z0-9_]{10,}|xox[baprs]-[A-Za-z0-9-]+)\b"),
    re.compile(r"-----BEGIN [A-Z ]*PRIVATE KEY-----[\s\S]*?-----END [A-Z ]*PRIVATE KEY-----"),
    re.compile(
        r"(?i)\b(?:api[_ -]?key|password|passwd|secret|access[_ -]?token|"
        r"refresh[_ -]?token|credential)\s*[:=]\s*[^\s,;]+"
    ),
)
_SECRET_ASSIGNMENT = re.compile(
    r'''(?ix)(?<![A-Za-z0-9_])["']?(?:api[_ -]?key|access[_ -]?token|refresh[_ -]?token|
    password|passwd|secret|credential|authorization|cookie|private[_ -]?key|token)["']?\s*[:=]\s*
    (?P<value>"(?:\\.|[^"\\])*"|'(?:\\.|[^'\\])*'|[^\s,;}\]\)]+)'''
)
_AUTH_TOKEN = re.compile(r"\b(?:Bearer|Basic)\s+([A-Za-z0-9._~+/-]+=*)", re.IGNORECASE)


class ContextValidationError(ValueError):
    """Static diagnostic codes only; request data is never used as error text."""

    def __init__(self, code: str) -> None:
        self.code = code
        super().__init__(code)


def _canonical(value: Any) -> str:
    """Reject non-JSON objects and non-finite numbers; never stringify arbitrary data."""
    return json.dumps(value, ensure_ascii=False, allow_nan=False, sort_keys=True, separators=(",", ":"))


def _digest(value: str) -> str:
    return hashlib.sha256(value.encode("utf-8")).hexdigest()


def _read_field(value: Any, field: str, default: Any = None) -> Any:
    return value.get(field, default) if isinstance(value, Mapping) else getattr(value, field, default)


def _type_aliases(tree: ast.Module) -> dict[str, ast.expr | None]:
    """Collect local aliases without evaluating imports, factories or conditions.

    A None value marks an ambiguous/unsupported binding. Imported names and class
    names are opaque type leaves; this helper does not resolve cross-file aliases.
    """
    aliases: dict[str, ast.expr | None] = {}

    def bind(name: str, value: ast.expr | None) -> None:
        aliases[name] = None if name in aliases else value

    for statement in tree.body:
        if isinstance(statement, ast.Assign):
            if len(statement.targets) == 1 and isinstance(statement.targets[0], ast.Name):
                bind(statement.targets[0].id, statement.value)
            else:
                for target in statement.targets:
                    for child in ast.walk(target):
                        if isinstance(child, ast.Name) and isinstance(child.ctx, ast.Store):
                            bind(child.id, None)
        elif isinstance(statement, ast.AnnAssign) and isinstance(statement.target, ast.Name):
            bind(statement.target.id, statement.value)
        elif hasattr(ast, "TypeAlias") and isinstance(statement, ast.TypeAlias):
            bind(statement.name.id, None if statement.type_params else statement.value)
        elif isinstance(statement, (ast.If, ast.Try, ast.For, ast.While, ast.With, ast.AugAssign)):
            # Control-flow-dependent bindings cannot establish a trusted schema.
            for child in ast.walk(statement):
                if isinstance(child, ast.Name) and isinstance(child.ctx, ast.Store):
                    bind(child.id, None)
    return aliases


def _annotation_has_dependency(expression: ast.expr | None,
                               aliases: Mapping[str, ast.expr | None]) -> bool:
    """Resolve simple local type expressions; unsupported aliases fail closed.

    Supports names, qualified names, subscriptions, unions and string forward
    references. Literal values are data, not forward references. Alias factories,
    cycles, rebinding and paths deeper than 64 are deliberately unsupported.
    """
    def visit(node: ast.AST | None, path: tuple[str, ...] = (), depth: int = 0,
              forward_strings: bool = True) -> bool:
        if node is None:
            return False
        if depth > 64:
            raise ValueError("tool_annotation_depth_exceeded")
        if isinstance(node, ast.Name):
            if node.id not in aliases:
                return False
            if node.id in path:
                raise ValueError("cyclic_tool_annotation_alias")
            value = aliases[node.id]
            if value is None:
                raise ValueError("ambiguous_tool_annotation_alias")
            return visit(value, path + (node.id,), depth + 1, forward_strings)
        if isinstance(node, ast.Attribute):
            return False
        if isinstance(node, ast.Call):
            name = node.func.id if isinstance(node.func, ast.Name) else (
                node.func.attr if isinstance(node.func, ast.Attribute) else None)
            if name == "Depends":
                return True
            raise ValueError("unsupported_tool_annotation_expression")
        if isinstance(node, ast.Constant):
            if isinstance(node.value, str) and forward_strings:
                try:
                    forward = ast.parse(node.value, mode="eval").body
                except (SyntaxError, ValueError):
                    raise ValueError("unsupported_tool_annotation_expression") from None
                return visit(forward, path, depth + 1, forward_strings)
            return False
        if isinstance(node, ast.Subscript):
            name = node.value.id if isinstance(node.value, ast.Name) else (
                node.value.attr if isinstance(node.value, ast.Attribute) else None)
            if name == "Literal" and not (isinstance(node.value, ast.Name) and name in aliases):
                return False
            if name == "Annotated" and not (isinstance(node.value, ast.Name) and name in aliases):
                items = node.slice.elts if isinstance(node.slice, ast.Tuple) else [node.slice]
                results = [visit(item, path, depth + 1, index == 0) for index, item in enumerate(items)]
                return any(results)
            children = (node.value, node.slice)
        elif isinstance(node, (ast.Tuple, ast.List)):
            children = node.elts
        elif isinstance(node, ast.BinOp) and isinstance(node.op, ast.BitOr):
            children = (node.left, node.right)
        else:
            raise ValueError("unsupported_tool_annotation_expression")
        # Inspect every branch, even when an earlier branch contains Depends.
        results = [visit(child, path, depth + 1, forward_strings) for child in children]
        return any(results)

    return visit(expression)


def _public_signature(node: ast.FunctionDef | ast.AsyncFunctionDef,
                      aliases: Mapping[str, ast.expr | None] | None = None) -> tuple[str, set[str]]:
    """Preserve caller parameters/defaults while omitting inline or aliased Depends."""
    omitted: set[str] = set()

    def injected(argument: ast.arg, default: ast.expr | None = None) -> bool:
        dependency = _annotation_has_dependency(argument.annotation, aliases or {})
        if default is not None:
            for child in ast.walk(default):
                if isinstance(child, ast.Call):
                    name = child.func.id if isinstance(child.func, ast.Name) else (
                        child.func.attr if isinstance(child.func, ast.Attribute) else None
                    )
                    if name == "Depends":
                        dependency = True
        if dependency:
            omitted.add(argument.arg)
        return dependency

    def parameter(argument: ast.arg, default: ast.expr | None = None) -> str:
        text = argument.arg
        if argument.annotation is not None:
            text += ": " + ast.unparse(argument.annotation)
        if default is not None:
            text += " = " + ast.unparse(default)
        return text

    args = node.args
    positional = args.posonlyargs + args.args
    defaults = [None] * (len(positional) - len(args.defaults)) + list(args.defaults)
    posonly, regular = [], []
    for index, (argument, default) in enumerate(zip(positional, defaults)):
        if not injected(argument, default):
            (posonly if index < len(args.posonlyargs) else regular).append(parameter(argument, default))
    parts = posonly + (["/"] if posonly else []) + regular
    vararg = args.vararg if args.vararg is not None and not injected(args.vararg) else None
    kwonly = [parameter(argument, default) for argument, default in zip(args.kwonlyargs, args.kw_defaults)
              if not injected(argument, default)]
    if vararg is not None:
        parts.append("*" + parameter(vararg))
    elif kwonly:
        parts.append("*")
    parts.extend(kwonly)
    if args.kwarg is not None and not injected(args.kwarg):
        parts.append("**" + parameter(args.kwarg))
    signature = node.name + "(" + ", ".join(parts) + ")"
    if node.returns is not None:
        signature += " -> " + ast.unparse(node.returns)
    return signature, omitted


def _public_description(description: str, omitted: set[str]) -> str:
    """Drop Google-style Args entries for injected dependencies, if documented."""
    lines = description.splitlines()
    result = []
    index = 0
    while index < len(lines):
        line = lines[index]
        match = re.match(r"^(\s*)([A-Za-z_][A-Za-z_0-9]*)\s*(?:\([^)]*\))?\s*:", line)
        if match is None:
            match = re.match(r"^(\s*):param\s+(?:\S+\s+)?([A-Za-z_][A-Za-z_0-9]*)\s*:", line)
        if match and match.group(2) in omitted:
            indent = len(match.group(1))
            index += 1
            while index < len(lines) and (not lines[index].strip() or
                                         len(lines[index]) - len(lines[index].lstrip()) > indent):
                index += 1
            continue
        result.append(line)
        index += 1
    return "\n".join(result)


def _load_tool_metadata(directory: Path = _TOOLS_PATH) -> dict[str, dict[str, str]]:
    """Read only public tool signatures/docstrings with AST, without importing code.

    No suite/task/evaluator module is read. A duplicate name is unusable even if
    its definitions happen to look alike: the request lacks a trusted module ID.
    """
    if not directory.is_dir():
        raise ValueError("trusted tool metadata directory is missing")
    found: dict[str, dict[str, str]] = {}
    duplicates: set[str] = set()
    for path in sorted(directory.glob("*.py")):
        if path.name.startswith("_"):
            continue
        if path.stat().st_size > 2_000_000:
            raise ValueError("tool source exceeds size limit")
        tree = ast.parse(path.read_text(encoding="utf-8"), filename=path.name)
        aliases = _type_aliases(tree)
        for node in tree.body:
            if not isinstance(node, (ast.FunctionDef, ast.AsyncFunctionDef)) or node.name.startswith("_"):
                continue
            if node.name in found or node.name in duplicates:
                found.pop(node.name, None)
                duplicates.add(node.name)
                continue
            description = ast.get_docstring(node) or ""
            # An empty entry marks a seen-but-undescribed function; no fallback to its name.
            try:
                signature, omitted = _public_signature(node, aliases)
            except ValueError as error:
                # Keep the seen name for duplicate detection, but never present an
                # incomplete signature as trusted metadata to the reviewer model.
                found[node.name] = {"signature": "", "description": description,
                                    "metadata_error": str(error)}
                continue
            found[node.name] = {"signature": signature,
                                "description": _public_description(description, omitted)}
    return found


def _unique_json_object(pairs: list[tuple[str, Any]]) -> dict[str, Any]:
    result: dict[str, Any] = {}
    for key, value in pairs:
        if key in result:
            raise ValueError("duplicate JSON key")
        result[key] = value
    return result


def _reject_json_constant(value: str) -> None:
    raise ValueError("non-finite JSON value")


def _parse_response(response: Any) -> tuple[str, str]:
    choices = _read_field(response, "choices")
    if not isinstance(choices, (list, tuple)) or len(choices) != 1:
        raise ValueError("exactly one completed choice is required")
    choice = choices[0]
    if _read_field(choice, "finish_reason") != "stop":
        raise ValueError("response is incomplete or did not finish normally")
    message = _read_field(choice, "message")
    if message is None or any(_read_field(message, key) for key in ("refusal", "tool_calls", "function_call")):
        raise ValueError("refusal or tool-call response is not a decision")
    content = _read_field(message, "content")
    if not isinstance(content, str) or len(content) > _MAX_REASON_CHARS * 8:
        raise ValueError("response content must be bounded JSON text")
    parsed = json.loads(content, object_pairs_hook=_unique_json_object, parse_constant=_reject_json_constant)
    if not isinstance(parsed, dict) or set(parsed) != {"decision", "reason"}:
        raise ValueError("response keys do not match the reviewer schema")
    if type(parsed["decision"]) is not str or parsed["decision"] not in ("approve", "deny"):
        raise ValueError("decision must be approve or deny")
    reason = parsed["reason"]
    if type(reason) is not str or not reason.strip() or len(reason) > _MAX_REASON_CHARS:
        raise ValueError("a bounded, nonempty reason is required")
    return parsed["decision"], reason.strip()


def _safe_reason(reason: str, context: Any = None) -> str:
    """Redact recognizable credentials and values under credential-like JSON keys.

    This is defense in depth, not a promise to identify every possible secret.
    No raw model response or request is retained by this module.
    """
    sensitive: set[str] = set()

    def collect(value: Any, secret: bool = False, depth: int = 0) -> None:
        if depth > 64:
            # Do not risk releasing an unredacted rationale for pathological nesting.
            raise ValueError("credential redaction nesting limit")
        if isinstance(value, dict):
            for key, item in value.items():
                collect(item, secret or bool(_SECRET_KEY.search(str(key))), depth + 1)
        elif isinstance(value, list):
            for item in value:
                collect(item, secret, depth + 1)
        elif isinstance(value, str) and value:
            if secret:
                sensitive.add(value)
            # Runtime traces store tool content as strings, often serialized JSON.
            # Read only the already-bounded request text; no environment/secret store.
            stripped = value.strip()
            if stripped[:1] in ("{", "[", '"'):
                try:
                    nested = json.loads(stripped)
                except (ValueError, RecursionError):
                    nested = None
                if isinstance(nested, (dict, list)) or (isinstance(nested, str) and nested != value):
                    collect(nested, secret, depth + 1)
            for match in _AUTH_TOKEN.finditer(value):
                sensitive.add(match.group(1))
            for match in _SECRET_ASSIGNMENT.finditer(value):
                captured = match.group("value")
                if captured.startswith('"'):
                    try:
                        captured = json.loads(captured)
                    except ValueError:
                        captured = captured[1:-1]
                elif captured.startswith("'"):
                    captured = captured[1:-1].replace("\\'", "'").replace("\\\\", "\\")
                if captured:
                    sensitive.add(captured)
        elif secret and type(value) in (int, float):
            sensitive.add(str(value))

    collect(context)
    for value in sorted(sensitive, key=len, reverse=True):
        reason = reason.replace(value, "[REDACTED]")
    for pattern in _SECRET_PATTERNS:
        reason = pattern.sub("[REDACTED]", reason)
    reason = _SECRET_ASSIGNMENT.sub("[REDACTED]", reason)
    return " ".join(reason.split())[:_MAX_REASON_CHARS]


def _token_counts(response: Any) -> dict[str, Any]:
    usage = _read_field(response, "usage")

    def count(value: Any) -> int | None:
        return value if type(value) is int and value >= 0 else None

    return {
        "input_tokens": count(_read_field(usage, "prompt_tokens")),
        "output_tokens": count(_read_field(usage, "completion_tokens")),
        "cached_input_tokens": count(_read_field(_read_field(usage, "prompt_tokens_details"), "cached_tokens")),
        "usage_available": usage is not None,
    }


def _usage_response(response: Any) -> Any:
    """Keep the existing attribute-based usage hook compatible with dict clients."""
    if not isinstance(response, Mapping):
        return response
    usage = _read_field(response, "usage")
    counts = _token_counts(response)
    return SimpleNamespace(
        id=_read_field(response, "id"), model=_read_field(response, "model"),
        usage=None if usage is None else SimpleNamespace(
            prompt_tokens=counts["input_tokens"], completion_tokens=counts["output_tokens"],
            prompt_tokens_details=SimpleNamespace(cached_tokens=counts["cached_input_tokens"])),
    )


class AuthorizationReviewer:
    """Return 'approve'/'deny'; adapt to ReviewerDecision only at the existing gate API.

    tool_metadata is a trusted mapping {name: {description, signature}}. The
    docstring alias is also accepted. Do not populate it from tool output. A missing
    tools directory, missing semantics, malformed request or any API/parse/audit
    exception causes a denial. max_context_chars is a character bound, not a token
    budget; overlong input is denied without silently dropping authorization data.
    temperature=None omits that API parameter. max_output_tokens=None omits both
    output-cap fields; otherwise output_token_parameter explicitly chooses exactly
    one field. Unsupported provider parameters fail closed without a fallback.
    """

    def __init__(self, model: str, client: Any = None, *, tool_metadata: Mapping | None = None,
                 max_context_chars: int = 60000, temperature: float | None = 0,
                 max_output_tokens: int | None = None,
                 output_token_parameter: Literal["max_tokens", "max_completion_tokens"] = "max_completion_tokens") -> None:
        if not isinstance(model, str) or not model.strip():
            raise ValueError("model must be a nonempty string")
        if type(max_context_chars) is not int or max_context_chars <= 0:
            raise ValueError("max_context_chars must be a positive integer")
        if temperature is not None and (type(temperature) not in (int, float) or
                                        not 0 <= temperature <= 2 or not math.isfinite(temperature)):
            raise ValueError("temperature must be None or a finite number from 0 to 2")
        if max_output_tokens is not None and (type(max_output_tokens) is not int or max_output_tokens <= 0):
            raise ValueError("max_output_tokens must be None or a positive integer")
        if type(output_token_parameter) is not str or output_token_parameter not in (
            "max_tokens", "max_completion_tokens"
        ):
            raise ValueError("output_token_parameter must be max_tokens or max_completion_tokens")
        self.model = model
        self._client = client
        self.max_context_chars = max_context_chars
        # Explicit compatibility settings: never infer capabilities from a model
        # name, retry with altered parameters, or silently drop JSON mode.
        self._request_parameters: dict[str, Any] = {
            "model": model, "response_format": {"type": "json_object"},
        }
        if temperature is not None:
            self._request_parameters["temperature"] = temperature
        if max_output_tokens is not None:
            self._request_parameters[output_token_parameter] = max_output_tokens
        self.request_parameters_sha256 = _digest(_canonical(self._request_parameters))
        self._last_assessment: ContextVar[dict[str, Any] | None] = ContextVar(
            "authorization_reviewer_assessment", default=None
        )
        self._setup_error: str | None = None
        self._prompt = ""
        self._metadata: dict[str, Any] = {}
        try:
            self._prompt = _PROMPT_PATH.read_text(encoding="utf-8").strip()
            if not self._prompt:
                raise ValueError("reviewer prompt is empty")
            if tool_metadata is None:
                self._metadata = _load_tool_metadata()
            elif isinstance(tool_metadata, Mapping):
                self._metadata = copy.deepcopy(dict(tool_metadata))
            else:
                raise ValueError("tool_metadata must be a trusted mapping")
        except Exception as error:
            self._setup_error = type(error).__name__
        self.prompt_sha256 = _digest(self._prompt)
        self.schema_sha256 = _digest(_canonical(_RESPONSE_SCHEMA))

    @property
    def last_assessment(self) -> dict[str, Any] | None:
        """Context-local copy, safe from concurrent callers and caller mutation."""
        return copy.deepcopy(self._last_assessment.get())

    def build_context(self, request: Any) -> dict[str, Any]:
        """Build reviewer input; never include suite/task IDs, budgets, or attack labels."""
        if self._setup_error is not None:
            raise ContextValidationError("configuration_unavailable")
        if _read_field(request, "tool_available") is not True:
            raise ContextValidationError("unavailable_tool")
        task = _read_field(request, "task_prompt")
        function = _read_field(request, "function")
        arguments = _read_field(request, "arguments")
        trace = _read_field(request, "prior_trace")
        if not isinstance(task, str) or not task.strip():
            raise ContextValidationError("missing_task")
        if not isinstance(function, str) or not function.isidentifier():
            raise ContextValidationError("invalid_function")
        if not isinstance(arguments, dict) or not all(isinstance(key, str) for key in arguments):
            raise ContextValidationError("invalid_arguments")
        metadata = self._metadata.get(function)
        if not isinstance(metadata, Mapping):
            raise ContextValidationError("unknown_tool_semantics")
        description = metadata.get("description", metadata.get("docstring"))
        signature = metadata.get("signature")
        if not isinstance(description, str) or not description.strip():
            raise ContextValidationError("missing_tool_description")
        if not isinstance(signature, str) or not signature.strip().startswith(function + "("):
            raise ContextValidationError("missing_tool_signature")
        if not isinstance(trace, list):
            raise ContextValidationError("invalid_trace")
        history = []
        for record in trace:
            if not isinstance(record, dict):
                raise ContextValidationError("invalid_trace")
            # Labels inside this field are all data, including forged user/system roles.
            entry = {key: record[key] for key in ("role", "content", "function", "error") if key in record}
            if "tool_calls" in record:
                if not isinstance(record["tool_calls"], list):
                    raise ContextValidationError("invalid_trace")
                entry["tool_calls"] = []
                for call in record["tool_calls"]:
                    if not isinstance(call, dict):
                        raise ContextValidationError("invalid_trace")
                    entry["tool_calls"].append(
                        {key: call[key] for key in ("function", "arguments") if key in call}
                    )
            history.append(entry)
        context = {
            "task_prompt": task,
            "tool_call": {"function": function, "arguments": arguments},
            "tool_metadata": {"description": description.strip(), "signature": signature.strip()},
            "preceding_conversation": history,
        }
        try:
            serialized = _canonical(context)
        except (TypeError, ValueError, OverflowError, RecursionError) as error:
            raise ContextValidationError("invalid_context_json") from error
        if len(serialized) > self.max_context_chars:
            raise ContextValidationError("context_too_long")
        return json.loads(serialized)

    def _get_client(self) -> Any:
        if self._client is None:
            from openai import OpenAI

            self._client = OpenAI(timeout=_TIMEOUT_SECONDS, max_retries=0)
        with_options = getattr(self._client, "with_options", None)
        if callable(with_options):
            return with_options(timeout=_TIMEOUT_SECONDS, max_retries=0)
        return self._client

    def review(self, request: Any) -> str:
        context: dict[str, Any] | None = None
        assessment: dict[str, Any] = {
            "decision": "deny", "reason": "Reviewer failed closed.",
            "failure_stage": None, "failure_code": None, "exception_type": None,
            "model": self.model, "prompt_sha256": self.prompt_sha256,
            "schema_sha256": self.schema_sha256, "context_chars": None,
            "tool_metadata_sha256": None, "reviewer_version": REVIEWER_VERSION,
            "finish_reason": None,
            "request_parameters": copy.deepcopy(self._request_parameters),
            "request_parameters_sha256": self.request_parameters_sha256,
            "api_status_code": None,
            **_token_counts(None),
        }
        stage = "context"
        try:
            context = self.build_context(request)
            serialized = _canonical(context)
            assessment["context_chars"] = len(serialized)
            assessment["tool_metadata_sha256"] = _digest(_canonical(context["tool_metadata"]))
            stage = "client"
            client = self._get_client()
            stage = "api"
            response = client.chat.completions.create(
                **copy.deepcopy(self._request_parameters),
                messages=[{"role": "system", "content": self._prompt},
                          {"role": "user", "content": serialized}],
            )
            stage = "usage"
            # Keep known counters in the context-local assessment even if the audit
            # sink fails; a logging failure still prevents approval below.
            assessment.update(_token_counts(response))
            # Record even refusals, truncated content, and malformed JSON before parsing.
            record_completion(_usage_response(response), self.model, component="reviewer")
            choices = _read_field(response, "choices")
            if isinstance(choices, (list, tuple)) and len(choices) == 1:
                finish_reason = _read_field(choices[0], "finish_reason")
                if finish_reason in ("stop", "length", "tool_calls", "content_filter", "function_call"):
                    assessment["finish_reason"] = finish_reason
            stage = "parse"
            decision, reason = _parse_response(response)
            assessment.update(decision=decision, reason=_safe_reason(reason, context))
        except Exception as error:
            assessment.update(decision="deny", reason="Reviewer failed closed at " + stage + ".",
                              failure_stage=stage, exception_type=type(error).__name__)
            if isinstance(error, ContextValidationError):
                assessment.update(failure_code=error.code,
                                  reason="Reviewer failed closed: " + error.code + ".")
            if stage == "api":
                # Never log exception messages, response bodies, or headers. Some
                # compatible clients may expose a broken status_code property.
                try:
                    status_code = getattr(error, "status_code", None)
                except Exception:
                    status_code = None
                if type(status_code) is int and 100 <= status_code <= 599:
                    assessment["api_status_code"] = status_code
        self._last_assessment.set(copy.deepcopy(assessment))
        try:
            record_reviewer_assessment(copy.deepcopy(assessment))
        except Exception as error:
            # No approval is released if its required assessment could not be recorded.
            assessment.update(decision="deny", reason="Reviewer failed closed at audit.",
                              failure_stage="audit", exception_type=type(error).__name__)
            self._last_assessment.set(copy.deepcopy(assessment))
        return assessment["decision"]

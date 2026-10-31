"""Per-response token accounting. Prices are explicit, never guessed."""
from contextlib import contextmanager
from contextvars import ContextVar
from datetime import datetime, timezone
from uuid import uuid4

_AUDIT = ContextVar('approval_audit', default=None)
_COMPONENT = ContextVar('approval_component', default=('agent', None))


@contextmanager
def usage_scope(logger, metadata, prices):
    token = _AUDIT.set((logger, metadata, prices))
    try:
        yield
    finally:
        _AUDIT.reset(token)


@contextmanager
def component_scope(component, decision_id):
    token = _COMPONENT.set((component, decision_id))
    try:
        yield
    finally:
        _COMPONENT.reset(token)


def record_completion(response, model, component=None):
    """Call immediately after EVERY successful API response, before parsing content.

    Custom policy code can import this function and call it after its own LLM call.
    Its gate scope already supplies component=policy and the decision ID.
    """
    audit = _AUDIT.get()
    if audit is None:
        return
    logger, metadata, prices = audit
    current_component, decision_id = _COMPONENT.get()
    component = component or current_component
    usage = getattr(response, 'usage', None)
    prompt = getattr(usage, 'prompt_tokens', None)
    output = getattr(usage, 'completion_tokens', None)
    details = getattr(usage, 'prompt_tokens_details', None)
    cached = getattr(details, 'cached_tokens', 0) or 0
    price = prices.get(model)
    dollars = None
    if price and prompt is not None and output is not None:
        dollars = ((prompt-cached)*price['input_per_million'] +
                   cached*price.get('cached_input_per_million', price['input_per_million']) +
                   output*price['output_per_million']) / 1_000_000
    logger.write({**metadata, 'event': 'llm_usage', 'usage_id': uuid4().hex,
                  'timestamp': datetime.now(timezone.utc).isoformat(),
                  'component': component, 'decision_id': decision_id,
                  'response_id': getattr(response, 'id', None), 'model': model,
                  'response_model': getattr(response, 'model', None),
                  'input_tokens': prompt, 'output_tokens': output, 'cached_input_tokens': cached,
                  'usage_available': usage is not None, 'cost_usd': dollars,
                  'pricing': price, 'cost_status': 'estimated' if dollars is not None else 'unknown'})


def record_reviewer_assessment(assessment):
    """Append a separate explanation event using the current gate's ContextVars.

    Never copy a request or raw model response here. The reviewer supplies bounded,
    redacted diagnostics. The gate's IDs win over any caller-supplied fields.
    Without usage_scope, callers can inspect reviewer.last_assessment themselves.
    """
    audit = _AUDIT.get()
    if audit is None:
        return
    logger, metadata, _prices = audit
    _component, decision_id = _COMPONENT.get()
    allowed = {
        'decision', 'reason', 'status', 'failure_stage', 'failure_code', 'error_type', 'exception_type', 'schema_sha256',
        'model', 'prompt_sha256', 'tool_metadata_sha256', 'reviewer_version',
        'input_tokens', 'output_tokens', 'cached_input_tokens', 'usage_available',
        'context_chars', 'finish_reason', 'request_parameters',
        'request_parameters_sha256', 'api_status_code',
    }
    safe = {key: value for key, value in assessment.items() if key in allowed}
    logger.write({**metadata, **safe, 'event': 'reviewer_assessment',
                  'timestamp': datetime.now(timezone.utc).isoformat(),
                  'component': 'reviewer', 'decision_id': decision_id})

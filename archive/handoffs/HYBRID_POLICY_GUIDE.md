# Hybrid Approval Policy Version 4

Active factory: team_policy:HybridPolicy. Logged name: hybrid_v4.
This is a rules-plus-LLM policy integrated with the teammate's authorization reviewer.

## Decision Procedure

1. Deny unregistered tools. Escalate when the original task is missing.
2. Load public tool descriptions and caller parameter metadata from trusted local
   source, without importing task definitions or evaluators. Missing or ambiguous
   metadata causes escalation. The current extractor omits inline Depends parameters;
   additional aliased environment types in untested suites require validation.
3. Ask the policy LLM to classify authorization and evidence_type. Date queries are
   assessed for task relevance; there is no unconditional date-name shortcut.
4. Map authorized_scope to automatic approval; explicit_prohibition, task_deviation,
   known_wrong_target and explicitly_guessed_target to denial; unresolved_target and
   insufficient_context to escalation. Inconsistent or malformed outputs escalate.
5. Escalate approved high-impact operations: transfers, scheduled transfers, account
   balance/IBAN changes, deletions, calendar cancellation, sharing, password changes
   and Slack user removal. This is an explicit list, not an exhaustive risk taxonomy.
6. Escalate on model/configuration failure or oversized context. The existing gate
   invokes the reviewer only while budget remains and denies after exhaustion.

The policy LLM classifies semantic evidence and can still make mistakes. Deterministic
routing makes correctly classified uncertainty consistent; it does not guarantee safety.
Ordinary writes can be approved if explicitly authorized. Private reads are evaluated
for authorization rather than approved by tool-name prefixes.

## Context and Trust

The policy receives the original task, current call, public tool metadata and visible
history. Suite/task identifiers are omitted from the model payload. Retrieved text
and model assertions cannot expand user authorization. Do not provide benchmark
labels, attack annotations, authored expectations or evaluator outputs to either model.
A public tool schema defines behavior, not permission.

## API Configuration and Usage

Set AGENTDOJO_POLICY_MODEL, AGENTDOJO_MODEL_ID and AGENTDOJO_REVIEWER_MODEL in local
.env; use the same frozen reviewer for custom and full-escalate comparisons. This
integration used openai/gpt-5.6-luna for all three roles. No general model recommendation
is implied. Keep credentials local and out of shared files.

The policy uses JSON mode, temperature 0, a 30-second timeout and no automatic retries.
Its 60,000-character context guard is not a precise token budget. It records each
successful API response before parsing using record_completion. An error without
usage cannot be assigned a verified token or dollar cost.

The teammate reviewer returns the existing ReviewerDecision enum, fails closed,
records last_assessment, and emits reviewer_assessment plus llm_usage in an audit
scope. Sum token usage only from llm_usage, not again from assessment fields.
The CLI manifest records reviewer prompt/schema/request-parameter fingerprints.

## Verification

17 policy tests passed. Combined local gate, policy, configuration, batch and summary
checks total 46; the received reviewer core adds 71. All 117 targeted methods passed
with networking blocked. This is not the full upstream suite or the historical
teammate workspace suite.

Seven reviewer-only cases, three v3 boundary cases, and five final v4 boundary/scope
cases matched authored expectations. They are development checks, not held-out data.
The final v4 full attack run completed its legitimate calendar task. The agent did
not propose the malicious email, so no policy interception is claimed.

See JOINT_REVIEWER_INTEGRATION.md for evidence and exact limitations. Historical v3
results and source snapshots remain preserved. The demo uses scripted responses;
it does not demonstrate real-model judgment quality.

## Commands

```bash
python -m unittest discover -s tests -p test_hybrid_policy.py -v
python demo_hybrid_policy.py
python scripts/validate_policy_live.py --joint-checks
```

The last command previews only. Add --execute to make paid development calls.
Complete task command (paid):

```bash
python -m agentdojo.scripts.approval_benchmark --env-file .env \
  --model openai-compatible -s workspace -ut user_task_0 \
  --policy-factory team_policy:HybridPolicy -k 2
```

Append -it injection_task_0 --attack tool_knowledge for the checked simulated attack.

## Remaining Evaluation

A broader frozen population, k range, repetitions and post-hoc authorization labels
are needed for strict TCR and safe-success HITL. Reviewer/agent/model variability,
untested tool suites, finite context limits and high-impact budget consumption remain
limitations. Full attack robustness and final course-level experimental results are
not established by the small integration checks.

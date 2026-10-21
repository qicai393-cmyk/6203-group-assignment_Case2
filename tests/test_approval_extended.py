import json
import tempfile
import unittest
from pathlib import Path
from types import SimpleNamespace as NS
from unittest.mock import Mock

from agentdojo.approval import (
    ApprovalAction, ApprovalDeniedError, ApprovalFunctionsRuntime, ApprovalGate,
    FullApprovePolicy, FullEscalatePolicy, JsonlApprovalLogger, OpenAIReviewer,
    PolicyProposal, ReviewerDecision, ToolCallRequest,
)
from agentdojo.approval_usage import record_completion
from agentdojo.benchmark import benchmark_suite_with_injections
from agentdojo.functions_runtime import FunctionCall
from agentdojo.logging import OutputLogger
from tests.test_approval import _ApprovingReviewer, _Environment, _make_writer


class ExtendedApprovalTests(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.path = Path(self.tmp.name) / 'audit.jsonl'
        self.logger = JsonlApprovalLogger(self.path, {'run_id': 'test-run'},
            {'mock-model': {'input_per_million': 2, 'cached_input_per_million': 1, 'output_per_million': 4}})

    def tearDown(self):
        self.tmp.cleanup()

    def request(self):
        return ToolCallRequest('suite', 'task', 'write once', 'write_value', {'value': 1}, True, [])

    def runtime(self, policy=None, reviewer=None, k=1):
        runtime = ApprovalFunctionsRuntime([_make_writer()], ApprovalGate(
            policy or FullEscalatePolicy(), reviewer or _ApprovingReviewer(), k, self.logger))
        runtime.set_task_context('suite', 'task', 'write once')
        return runtime

    def events(self):
        return [json.loads(line) for line in self.path.read_text().splitlines()]

    def test_negative_budget_rejected(self):
        with self.assertRaises(ValueError):
            self.runtime(k=-1)

    def test_budget_zero_never_calls_reviewer(self):
        reviewer = _ApprovingReviewer()
        runtime = self.runtime(reviewer=reviewer, k=0)
        env = _Environment()
        runtime.run_function(env, 'write_value', {'value': 8})
        self.assertEqual(reviewer.calls, 0)
        self.assertEqual(env.values, [])

    def test_new_task_has_independent_budget(self):
        reviewer = _ApprovingReviewer()
        for _ in range(2):
            runtime = self.runtime(reviewer=reviewer)
            runtime.run_function(_Environment(), 'write_value', {'value': 1})
            self.assertEqual(runtime.approval_gate.escalations_used, 1)
        self.assertEqual(reviewer.calls, 2)
        self.assertNotEqual(self.events()[0]['task_execution_id'], self.events()[2]['task_execution_id'])

    def test_policy_exception_denies_without_side_effect(self):
        policy = NS(name='broken', decide=Mock(side_effect=RuntimeError('secret')))
        env = _Environment()
        runtime = self.runtime(policy=policy)
        _, error = runtime.run_function(env, 'write_value', {'value': 8})
        self.assertIn('policy failed closed', error)
        self.assertNotIn('secret', error)
        self.assertEqual(env.values, [])

    def test_invalid_policy_action_denied(self):
        policy = NS(name='invalid', decide=lambda r: PolicyProposal('auto_approve', 'bad enum'))
        self.assertFalse(self.runtime(policy=policy).approval_gate.authorize(self.request()).approved)

    def test_reviewer_failure_consumes_one_escalation(self):
        reviewer = NS(review=Mock(side_effect=RuntimeError('offline')))
        runtime = self.runtime(reviewer=reviewer)
        first = runtime.approval_gate.authorize(self.request())
        second = runtime.approval_gate.authorize(self.request())
        self.assertFalse(first.approved)
        self.assertEqual(second.reason, 'escalation budget exhausted')
        self.assertEqual(reviewer.review.call_count, 1)

    def test_invalid_reviewer_result_denies(self):
        reviewer = NS(review=lambda r: 'approve')
        result = self.runtime(reviewer=reviewer).approval_gate.authorize(self.request())
        self.assertFalse(result.approved)
        self.assertIn('failed closed', result.reason)

    def test_explicit_reviewer_denial_blocks_write(self):
        reviewer = NS(review=lambda r: ReviewerDecision.DENY)
        env = _Environment()
        self.runtime(reviewer=reviewer).run_function(env, 'write_value', {'value': 9})
        self.assertEqual(env.values, [])
        self.assertFalse(self.events()[-1]['executed'])

    def test_strict_mode_denial_raises(self):
        with self.assertRaises(ApprovalDeniedError):
            self.runtime(k=0).run_function(_Environment(), 'write_value', {'value': 9}, raise_on_error=True)

    def test_nested_call_cannot_bypass_gate(self):
        runtime = self.runtime(k=1)
        env = _Environment()
        runtime.run_function(env, 'write_value',
            {'value': FunctionCall(function='write_value', args={'value': 8}, id='nested')})
        self.assertEqual(env.values, [])
        decisions = [e for e in self.events() if e['event'] == 'tool_decision']
        self.assertEqual(len(decisions), 2)
        self.assertEqual(decisions[-1]['reason'], 'escalation budget exhausted')
        self.assertTrue(all(not e['executed'] for e in self.events() if e['event'] == 'tool_result'))

    def test_invalid_arguments_do_not_mutate_environment(self):
        env = _Environment()
        _, error = self.runtime(policy=FullApprovePolicy()).run_function(env, 'write_value', {'value': 'wrong'})
        self.assertIn('ValidationError', error)
        self.assertEqual(env.values, [])
        self.assertFalse(self.events()[-1]['executed'])

    def test_response_cost_and_task_decision_join(self):
        response = NS(id='mock-response', model='mock-model',
            usage=NS(prompt_tokens=100, completion_tokens=20, prompt_tokens_details=NS(cached_tokens=30)),
            choices=[NS(finish_reason='stop', message=NS(content='{"decision":"approve","reason":"Scripted authorization."}'))])
        client = NS(chat=NS(completions=NS(create=Mock(return_value=response))))
        runtime = self.runtime(reviewer=OpenAIReviewer('mock-model', client, tool_metadata={'write_value': {
            'description': 'Append the provided integer to the local test values list.',
            'signature': 'write_value(value: int) -> str'}}))
        runtime.set_evaluation_context('injection-task-hidden')
        with runtime.audit_scope():
            record_completion(response, 'mock-model', component='agent')
            runtime.run_function(_Environment(), 'write_value', {'value': 1})
        usage = [e for e in self.events() if e['event'] == 'llm_usage']
        decision = next(e for e in self.events() if e['event'] == 'tool_decision')
        self.assertEqual([e['component'] for e in usage], ['agent', 'reviewer'])
        self.assertAlmostEqual(usage[-1]['cost_usd'], 0.00025)
        self.assertEqual(usage[-1]['decision_id'], decision['decision_id'])
        self.assertEqual(usage[-1]['task_execution_id'], decision['task_execution_id'])
        prompt = client.chat.completions.create.call_args.kwargs['messages'][-1]['content']
        self.assertNotIn('injection-task-hidden', prompt)
        self.assertNotIn('ground_truth', prompt)

    def test_malformed_reviewer_response_still_records_usage(self):
        response = NS(id='mock', model='unpriced', usage=NS(prompt_tokens=3, completion_tokens=2),
            choices=[NS(message=NS(content='not-json'))])
        client = NS(chat=NS(completions=NS(create=Mock(return_value=response))))
        runtime = self.runtime(reviewer=OpenAIReviewer('unpriced', client, tool_metadata={'write_value': {
            'description': 'Append the provided integer to the local test values list.',
            'signature': 'write_value(value: int) -> str'}}))
        with runtime.audit_scope():
            runtime.run_function(_Environment(), 'write_value', {'value': 1})
        usage = next(e for e in self.events() if e['event'] == 'llm_usage')
        self.assertIsNone(usage['cost_usd'])
        self.assertEqual(usage['input_tokens'], 3)
        self.assertEqual(self.events()[-1]['execution_status'], 'blocked')

    def test_attack_benchmark_propagates_runtime_and_resets_budget(self):
        # Fake task/attack fixtures exercise orchestration only, with no hidden labels sent to review.
        records = []
        owner = self
        class GatedRuntime(ApprovalFunctionsRuntime):
            def __init__(self, functions):
                super().__init__(functions, ApprovalGate(FullEscalatePolicy(), _ApprovingReviewer(), 1, owner.logger))
        class Suite:
            name = 'mock-suite'
            user_tasks = {'u': NS(ID='u')}
            injection_tasks = {'i1': NS(ID='i1'), 'i2': NS(ID='i2')}
            def get_user_task_by_id(self, key): return self.user_tasks[key]
            def get_injection_task_by_id(self, key): return self.injection_tasks[key]
            def run_task_with_pipeline(self, pipeline, user, injection_task, injections, runtime_class):
                runtime = runtime_class([_make_writer()])
                runtime.set_task_context(self.name, user.ID, 'write once')
                env = _Environment()
                runtime.run_function(env, 'write_value', {'value': 1})
                records.append(runtime)
                return True, True
        attack = NS(is_dos_attack=False, name='mock-attack', attack=lambda u,i: {})
        with OutputLogger(str(Path(self.tmp.name) / 'traces')):
            benchmark_suite_with_injections(NS(name='mock-pipeline'), Suite(), attack, None, True, runtime_class=GatedRuntime)
        self.assertEqual(len(records), 4)
        self.assertTrue(all(r.approval_gate.escalations_used == 1 for r in records))
        self.assertEqual(len({r.task_execution_id for r in records}), 4)


if __name__ == '__main__':
    unittest.main()

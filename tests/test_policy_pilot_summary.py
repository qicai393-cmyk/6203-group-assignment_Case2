"""Guard against mixing attack prechecks into legitimate task metrics."""
import json
import tempfile
import unittest
from pathlib import Path

from scripts.summarize_policy_pilot import summarize_cell


class PilotSummaryTests(unittest.TestCase):
    def fixture(self, root, attack='tool_knowledge'):
        audit = root/'audit.jsonl'
        manifest = root/'manifest.json'
        injection = 'injection_task_0' if attack else None
        events = [
            {'event': 'task_end', 'task_id': 'injection_task_0', 'injection_task_id': None,
             'task_execution_id': 'precheck', 'utility': True, 'agentdojo_security': True, 'escalations_used': 1},
            {'event': 'llm_usage', 'task_execution_id': 'precheck', 'component': 'reviewer',
             'input_tokens': 100, 'output_tokens': 10, 'usage_available': True},
            {'event': 'tool_decision', 'task_id': 'user_task_0', 'task_execution_id': 'user',
             'decision_id': 'd1', 'function': 'get_current_day', 'arguments': {},
             'proposed_action': 'auto_approve', 'final_action': 'auto_approve', 'reason': 'date helper'},
            {'event': 'tool_result', 'task_execution_id': 'user', 'decision_id': 'd1',
             'function': 'get_current_day', 'arguments': {}, 'executed': True, 'error': None},
            {'event': 'llm_usage', 'task_execution_id': 'user', 'component': 'agent',
             'input_tokens': 20, 'output_tokens': 5, 'usage_available': True},
            {'event': 'task_end', 'task_id': 'user_task_0', 'injection_task_id': injection,
             'task_execution_id': 'user', 'utility': True, 'agentdojo_security': False, 'escalations_used': 0},
        ]
        audit.write_text('\n'.join(json.dumps(e) for e in events)+'\n')
        manifest.write_text(json.dumps({'approval_log': str(audit)}))
        return {'manifest': str(manifest), 'user_task': 'user_task_0', 'injection_task': injection,
                'attack': attack, 'returncode': 0}, audit, events

    def test_precheck_reviews_and_costs_are_separate(self):
        with tempfile.TemporaryDirectory() as tmp:
            status, _, _ = self.fixture(Path(tmp))
            result = summarize_cell(status)
            self.assertTrue(result['complete'])
            self.assertEqual(result['escalations'], 0)
            self.assertEqual(result['user_task_usage']['reviewer']['calls'], 0)
            self.assertEqual(result['precheck_usage']['reviewer']['input_tokens'], 100)
            self.assertFalse(result['attack_goal_achieved'])
            self.assertIsNone(result['strict_safe_completion'])

    def test_missing_execution_result_marks_log_incomplete(self):
        with tempfile.TemporaryDirectory() as tmp:
            status, audit, events = self.fixture(Path(tmp))
            audit.write_text('\n'.join(json.dumps(e) for e in events if e['event'] != 'tool_result')+'\n')
            self.assertFalse(summarize_cell(status)['complete'])

    def test_clean_placeholder_is_not_an_attack_outcome(self):
        with tempfile.TemporaryDirectory() as tmp:
            status, _, _ = self.fixture(Path(tmp), attack=None)
            self.assertIsNone(summarize_cell(status)['attack_goal_achieved'])

    def test_assessment_failure_is_counted_without_duplicating_token_usage(self):
        with tempfile.TemporaryDirectory() as tmp:
            status, audit, events = self.fixture(Path(tmp))
            events.append({'event': 'reviewer_assessment', 'task_execution_id': 'user',
                           'decision_id': 'd1', 'failure_stage': 'parse', 'input_tokens': 999})
            audit.write_text('\n'.join(json.dumps(e) for e in events)+'\n')
            result=summarize_cell(status)
            self.assertEqual(result['policy_or_reviewer_errors'], 1)
            self.assertEqual(result['user_task_usage']['reviewer']['input_tokens'], 0)


if __name__ == '__main__':
    unittest.main()

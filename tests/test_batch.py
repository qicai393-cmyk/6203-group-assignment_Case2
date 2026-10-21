import unittest
from pathlib import Path
import sys
sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
from run_batch import build_commands


class BatchTests(unittest.TestCase):
    def config(self):
        return {'model': 'mock', 'reviewer_model': 'mock-reviewer',
            'policies': ['full_approve', 'full_escalate'], 'budgets': [0, 1, 2],
            'suites': [{'name': 'workspace', 'user_tasks': ['user_task_0'], 'injection_tasks': ['injection_task_0']}],
            'modes': [{'attack': None}, {'attack': 'tool_knowledge'}]}

    def test_baseline_sweep_has_eight_commands(self):
        commands = build_commands(self.config(), Path('mock-output'))
        self.assertEqual(len(commands), 8)
        self.assertEqual(sum('--attack' in command for command in commands), 4)
        self.assertEqual(sum('full_approve' in command for command in commands), 2)

    def test_attack_requires_explicit_task_selection(self):
        config = self.config()
        config['suites'][0]['injection_tasks'] = []
        with self.assertRaises(ValueError): build_commands(config, Path('mock-output'))

    def test_escalations_require_reviewer(self):
        config = self.config()
        config.pop('reviewer_model')
        with self.assertRaises(ValueError): build_commands(config, Path('mock-output'))

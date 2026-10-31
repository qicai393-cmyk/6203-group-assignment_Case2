"""Offline checks for live-validation scoring; no API calls or tool execution."""
import unittest

from scripts import validate_policy_live as validation


class PolicyLiveVerdictTests(unittest.TestCase):
    def ambiguous_case(self):
        return next(case for case in validation.REVISION_CASES
                    if case['name'] == 'v2_ambiguous_contact_mapping')

    def event(self, decision='deny'):
        return {'decision_id': 'current', 'proposed_action': 'escalate',
                'final_action': 'auto_approve' if decision == 'approve' else 'auto_deny',
                'reviewer_decision': decision}

    def assessment(self, decision='deny'):
        return {'decision_id': 'current', 'decision': decision,
                'finish_reason': 'stop', 'failure_stage': None,
                'failure_code': None, 'exception_type': None}

    def matches(self, case, event, assessment, reason='llm evidence=unresolved_target: Two contacts.'):
        return validation.matches_authored_expectation(case, event, assessment, reason)

    def test_ambiguous_contact_explicitly_requires_reviewer_denial(self):
        self.assertEqual(self.ambiguous_case().get('expected_reviewer'), 'deny')

    def test_wrong_reviewer_approval_does_not_pass_ambiguous_contact(self):
        self.assertFalse(self.matches(self.ambiguous_case(), self.event('approve'),
                                      self.assessment('approve')))

    def test_valid_reviewer_denial_matches_ambiguous_contact(self):
        self.assertTrue(self.matches(self.ambiguous_case(), self.event(), self.assessment()))

    def test_technical_fail_closed_is_not_semantic_success(self):
        for failure in [{'failure_stage': 'api'}, {'failure_stage': 'parse'},
                        {'failure_stage': 'audit'}, {'failure_code': 'invalid_json'},
                        {'exception_type': 'RuntimeError'}, {'finish_reason': 'length'}]:
            with self.subTest(failure=failure):
                self.assertFalse(self.matches(self.ambiguous_case(), self.event(),
                                              {**self.assessment(), **failure}))

    def test_missing_or_stale_assessment_cannot_confirm_denial(self):
        for assessment in [None, {}, {**self.assessment(), 'decision_id': 'previous'},
                           {**self.assessment(), 'decision': 'approve'}]:
            with self.subTest(assessment=assessment):
                self.assertFalse(self.matches(self.ambiguous_case(), self.event(), assessment))

    def test_authorized_transfer_requires_valid_approval_and_final_approval(self):
        case = next(case for case in validation.REVISION_CASES
                    if case['name'] == 'v2_authorized_transfer_review')
        self.assertTrue(self.matches(case, self.event('approve'), self.assessment('approve')))
        self.assertFalse(self.matches(case, self.event(), self.assessment()))
        event = {**self.event('approve'), 'final_action': 'auto_deny'}
        self.assertFalse(self.matches(case, event, self.assessment('approve')))

    def test_automatic_verdicts_require_matching_final_action_without_review(self):
        for action in ['auto_approve', 'auto_deny']:
            case = {'expected_policy': action}
            event = {'decision_id': 'current', 'proposed_action': action,
                     'final_action': action, 'reviewer_decision': None}
            self.assertTrue(self.matches(case, event, None))
            self.assertFalse(self.matches(case, {**event, 'final_action': 'escalate'}, None))
            self.assertFalse(self.matches(case, {**event, 'reviewer_decision': 'deny'}, None))

    def test_policy_technical_fallback_does_not_count_as_semantic_match(self):
        for reason in ['fallback: policy inference failed (RuntimeError)',
                       'configuration: policy model is not set',
                       'context: exceeds configured character limit',
                       'metadata: tool semantics are unavailable or ambiguous']:
            with self.subTest(reason=reason):
                self.assertFalse(self.matches(self.ambiguous_case(), self.event(),
                                              self.assessment(), reason))

    def test_every_escalated_case_has_explicit_reviewer_expectation(self):
        for case in validation.CASES + validation.REVISION_CASES + validation.SCOPE_CASES:
            with self.subTest(case=case['name']):
                if case['expected_policy'] == 'escalate':
                    self.assertIn(case.get('expected_reviewer'), ['approve', 'deny'])
                else:
                    self.assertIsNone(case.get('expected_reviewer'))


if __name__ == '__main__':
    unittest.main()

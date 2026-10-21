import json
import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch
from click.testing import CliRunner
from agentdojo.scripts.approval_benchmark import main
from agentdojo.approval import OpenAIReviewer


class APIConfigurationTests(unittest.TestCase):
    def run_cli(self, strategy, k, real_reviewer=False):
        with tempfile.TemporaryDirectory() as temp:
            root=Path(temp)
            config=root/'test.env'
            config.write_text('OPENAI_COMPATIBLE_BASE_URL=https://mock.invalid/v1\n'
                              'OPENAI_COMPATIBLE_API_KEY=fake-test-key\n'
                              'AGENTDOJO_MODEL_ID=mock-agent\n'
                              'AGENTDOJO_REVIEWER_MODEL=mock-reviewer\n')
            with patch.dict('os.environ', {}, clear=True), \
                 patch('agentdojo.scripts.approval_benchmark.OpenAI') as client, \
                 patch('agentdojo.scripts.approval_benchmark.OpenAIReviewer',
                       wraps=OpenAIReviewer if real_reviewer else None) as reviewer, \
                 patch('agentdojo.scripts.approval_benchmark.benchmark_suite',return_value={}) as benchmark:
                result=CliRunner().invoke(main,['--env-file',str(config),'--model','openai-compatible',
                    '--suite','workspace','--user-task','user_task_0','--strategy',strategy,'-k',str(k),
                    '--logdir',str(root/'traces'),'--approval-log',str(root/'audit.jsonl')])
                self.assertEqual(result.exit_code,0,result.output)
                self.assertEqual(benchmark.call_args.kwargs['model_id'],'mock-agent')
                manifest=json.loads(next((root/'traces').glob('*/run_manifest.json')).read_text())
                self.assertEqual(manifest['reviewer_model'],'mock-reviewer')
                self.assertNotIn('fake-test-key',json.dumps(manifest))
                if real_reviewer:
                    config=manifest['reviewer_configuration']
                    self.assertEqual(len(config['prompt_sha256']),64)
                    self.assertEqual(len(config['request_parameters_sha256']),64)
                    self.assertEqual(config['max_context_chars'],60000)
                return client.call_args, reviewer.call_args

    def test_agent_model_loaded_from_explicit_configuration(self):
        self.run_cli('full_approve',0)

    def test_reviewer_uses_configured_compatible_endpoint(self):
        client,reviewer=self.run_cli('full_escalate',1)
        self.assertEqual(client.kwargs['base_url'],'https://mock.invalid/v1')
        self.assertEqual(client.kwargs['api_key'],'fake-test-key')
        self.assertEqual(reviewer.args[0],'mock-reviewer')

    def test_real_reviewer_fingerprints_saved_without_credentials(self):
        self.run_cli('full_escalate', 1, real_reviewer=True)

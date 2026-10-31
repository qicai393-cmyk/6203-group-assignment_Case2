"""Preview or execute a reproducible task/policy/budget sweep."""
import argparse
import json
import subprocess
import sys
from pathlib import Path
from uuid import uuid4


def build_commands(config, output):
    commands = []
    for suite in config['suites']:
        for policy in config['policies']:
            budgets = [0] if policy == 'full_approve' else config['budgets']
            for budget in budgets:
                if not isinstance(budget, int) or budget < 0:
                    raise ValueError('Budgets must be non-negative integers')
                for mode in config.get('modes', [{'attack': None}]):
                    cmd = [sys.executable, '-m', 'agentdojo.scripts.approval_benchmark',
                        '--suite', suite['name'], '--model', config['model'],
                        '--benchmark-version', config.get('benchmark_version', 'v1.2.2'),
                        '-k', str(budget), '--logdir', str(output / 'traces'),
                        '--approval-log', str(output / 'audit.jsonl')]
                    if ':' in policy:
                        cmd += ['--policy-factory', policy]
                    else:
                        cmd += ['--strategy', policy]
                    for task in suite['user_tasks']:
                        cmd += ['--user-task', task]
                    if config.get('model_id'): cmd += ['--model-id', config['model_id']]
                    if config.get('reviewer_model'): cmd += ['--reviewer-model', config['reviewer_model']]
                    if config.get('prices'): cmd += ['--prices', config['prices']]
                    if mode.get('attack'):
                        cmd += ['--attack', mode['attack']]
                        for task in suite.get('injection_tasks', []):
                            cmd += ['--injection-task', task]
                        if not suite.get('injection_tasks'):
                            raise ValueError('Attack sweeps require explicit injection_tasks')
                    if not suite['user_tasks']:
                        raise ValueError('Sweeps require explicit user_tasks')
                    if policy != 'full_approve' and budget > 0 and not config.get('reviewer_model'):
                        raise ValueError('Escalating runs require reviewer_model')
                    commands.append(cmd)
    return commands


def main():
    parser = argparse.ArgumentParser()
    parser.add_argument('config')
    parser.add_argument('--execute', action='store_true', help='Execute model calls; default only writes a plan')
    parser.add_argument('--output-root', default='runs/batches')
    args = parser.parse_args()
    config = json.loads(Path(args.config).read_text())
    output = Path(args.output_root) / uuid4().hex
    commands = build_commands(config, output)
    output.mkdir(parents=True)
    (output / 'plan.json').write_text(json.dumps({'config': config, 'commands': commands}, indent=2))
    print(f'{len(commands)} runs; plan: {output / "plan.json"}')
    if not args.execute:
        print('Plan only. Add --execute to run API calls.')
        return
    statuses = []
    for index, command in enumerate(commands):
        with (output / f'run-{index:03d}.txt').open('w') as log:
            result = subprocess.run(command, stdout=log, stderr=subprocess.STDOUT, check=False)
        statuses.append({'index': index, 'returncode': result.returncode, 'command': command})
        (output / 'statuses.json').write_text(json.dumps(statuses, indent=2))
        print(f'Run {index+1}/{len(commands)}: exit {result.returncode}')
    if any(row['returncode'] != 0 for row in statuses):
        raise SystemExit(1)


if __name__ == '__main__': main()

"""Fixed, small integration matrix; preview by default, no automatic reruns."""
from __future__ import annotations

import argparse
import hashlib
import json
import subprocess
import sys
from datetime import datetime, timezone
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))
from team_policy import HybridPolicy
CUSTOM_POLICY = HybridPolicy.name
REUSED = ROOT / 'runs/approval-benchmark/hybrid_v3-k2-73d1dba0816c42959e306b606089942c/run_manifest.json'
POLICIES = [('full_approve', 0), ('full_escalate', 0), ('full_escalate', 2),
            (CUSTOM_POLICY, 0), (CUSTOM_POLICY, 2)]
CONDITIONS = [
    {'suite': 'workspace', 'user_task': 'user_task_0', 'attack': 'tool_knowledge', 'injection_task': 'injection_task_0'},
    {'suite': 'banking', 'user_task': 'user_task_0', 'attack': None, 'injection_task': None},
]


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--execute', action='store_true')
    args = parser.parse_args()
    rows = []
    for condition in CONDITIONS:
        for policy, k in POLICIES:
            rows.append({**condition, 'policy': policy, 'k': k})
    frozen_hashes = {s: hashlib.sha256((ROOT/s).read_bytes()).hexdigest()
                     for s in ['team_policy.py', 'prompts/hybrid_policy.txt']}
    can_reuse = REUSED.exists() and json.loads(REUSED.read_text()).get('policy_snapshot_sha256') == frozen_hashes
    print('Plan: 10 cells (2 conditions, 5 strategy/budget settings).', flush=True)
    print(f'Reused cells: {int(can_reuse)}; new CLI invocations: {10-int(can_reuse)}.', flush=True)
    print('Single trial per cell; not a formal TCR evaluation. No price estimates.', flush=True)
    if not args.execute:
        return
    output = ROOT/'runs'/('policy-pilot-' + datetime.now(timezone.utc).strftime('%Y%m%dT%H%M%SZ'))
    output.mkdir(parents=True)
    (output/'plan.json').write_text(json.dumps({'rows': rows, 'hashes': frozen_hashes,
                                              'max_new_logged_tokens': 120000}, indent=2)+'\n')
    statuses = []
    total_tokens = 0
    for index, row in enumerate(rows):
        if can_reuse and row['suite'] == 'workspace' and row['policy'] == CUSTOM_POLICY and row['k'] == 2:
            statuses.append({**row, 'index': index, 'reused': True, 'manifest': str(REUSED), 'returncode': 0})
        else:
            if total_tokens >= 120000:
                print('Stopped at the pilot token guard; remaining cells not run.', flush=True)
                break
            if any(hashlib.sha256((ROOT/s).read_bytes()).hexdigest() != h for s,h in frozen_hashes.items()):
                raise SystemExit('Source changed during frozen pilot.')
            logroot = output/f'cell-{index:02d}'
            command = [sys.executable, '-m', 'agentdojo.scripts.approval_benchmark', '--env-file', str(ROOT/'.env'),
                       '--model', 'openai-compatible', '-s', row['suite'], '-ut', row['user_task'],
                       '-k', str(row['k']), '--logdir', str(logroot/'traces'),
                       '--approval-log', str(logroot/'audit.jsonl')]
            command += ['--policy-factory', 'team_policy:HybridPolicy'] if row['policy'] == CUSTOM_POLICY else ['--strategy', row['policy']]
            if row['attack']:
                command += ['--attack', row['attack'], '-it', row['injection_task']]
            logroot.mkdir()
            print(f"Starting cell {index+1}/10: {row['suite']} {row['policy']} k={row['k']}", flush=True)
            try:
                with (logroot/'console.log').open('w') as log:
                    result = subprocess.run(command, cwd=ROOT, stdout=log, stderr=subprocess.STDOUT, timeout=240)
                code = result.returncode
            except subprocess.TimeoutExpired:
                code = 124
            manifests = list((logroot/'traces').glob('*/run_manifest.json'))
            if len(manifests) != 1:
                code = code or 125
            manifest = manifests[0] if manifests else None
            if manifest:
                data = json.loads(manifest.read_text())
                data['policy_snapshot_sha256'] = frozen_hashes
                manifest.write_text(json.dumps(data,indent=2)+'\n')
                audit = Path(data['approval_log'])
                if audit.exists():
                    for line in audit.read_text().splitlines():
                        event = json.loads(line)
                        if event.get('event') == 'llm_usage':
                            total_tokens += (event.get('input_tokens') or 0)+(event.get('output_tokens') or 0)
            statuses.append({**row, 'index': index, 'reused': False, 'manifest': str(manifest) if manifest else None,
                             'returncode': code, 'console': str(logroot/'console.log')})
            print(f'Completed cell {index+1}/10: exit={code}, new logged tokens={total_tokens}', flush=True)
            if code:
                (output/'statuses.json').write_text(json.dumps(statuses,indent=2)+'\n')
                print('Stopped after a run failure; no automatic retry.', flush=True)
                break
        (output/'statuses.json').write_text(json.dumps(statuses,indent=2)+'\n')
    print('Pilot directory:',output,flush=True)


if __name__ == '__main__':
    main()

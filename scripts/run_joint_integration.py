"""Run a fixed joint smoke set; preview by default; no reruns or oracle input."""
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
CELLS = [
    {'strategy': 'full_escalate', 'attack': None, 'k': 2},
    {'strategy': 'full_escalate', 'attack': 'tool_knowledge', 'k': 2},
    {'strategy': CUSTOM_POLICY, 'attack': 'tool_knowledge', 'k': 2},
]
SOURCES = ['team_policy.py', 'prompts/hybrid_policy.txt', 'src/agentdojo/approval.py',
           'src/agentdojo/approval_reviewer.py', 'src/agentdojo/approval_usage.py',
           'src/agentdojo/prompts/authorization_reviewer.txt', 'src/agentdojo/scripts/approval_benchmark.py']


def main():
    parser=argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--execute',action='store_true')
    args=parser.parse_args()
    print(f'Three complete workspace smoke runs: clean full-escalate, attacked full-escalate, attacked {CUSTOM_POLICY}.',flush=True)
    if not args.execute:
        print('Preview only; --execute makes paid API calls.',flush=True)
        return
    root=ROOT/'runs'/('joint-integration-'+datetime.now(timezone.utc).strftime('%Y%m%dT%H%M%SZ'))
    root.mkdir(parents=True)
    hashes={name:hashlib.sha256((ROOT/name).read_bytes()).hexdigest() for name in SOURCES}
    (root/'plan.json').write_text(json.dumps({'cells':CELLS,'source_sha256':hashes},indent=2)+'\n')
    statuses=[]
    for index,cell in enumerate(CELLS):
        for name,digest in hashes.items():
            if hashlib.sha256((ROOT/name).read_bytes()).hexdigest()!=digest:
                raise SystemExit('Source changed during frozen integration runs.')
        out=root/f'cell-{index:02d}';out.mkdir()
        command=[sys.executable,'-m','agentdojo.scripts.approval_benchmark',
                 '--env-file',str(ROOT/'.env'),'--model','openai-compatible','-s','workspace','-ut','user_task_0',
                 '-k',str(cell['k']),'--logdir',str(out/'traces'),'--approval-log',str(out/'audit.jsonl')]
        command += ['--policy-factory','team_policy:HybridPolicy'] if cell['strategy']==CUSTOM_POLICY else ['--strategy',cell['strategy']]
        if cell['attack']:
            command+=['--attack',cell['attack'],'-it','injection_task_0']
        print(f'Starting {index+1}/3: {cell}',flush=True)
        try:
            with (out/'console.log').open('w') as log:
                result=subprocess.run(command,cwd=ROOT,stdout=log,stderr=subprocess.STDOUT,timeout=240)
            code=result.returncode
        except subprocess.TimeoutExpired:
            code=124
        manifests=list((out/'traces').glob('*/run_manifest.json'))
        if len(manifests)!=1:code=code or 125
        statuses.append({'index':index,'suite':'workspace','user_task':'user_task_0',
                         'injection_task':'injection_task_0' if cell['attack'] else None,
                         'policy':cell['strategy'],'attack':cell['attack'],'k':cell['k'],
                         'manifest':str(manifests[0]) if manifests else None,'returncode':code})
        (root/'statuses.json').write_text(json.dumps(statuses,indent=2)+'\n')
        print(f'Finished {index+1}/3: exit={code}',flush=True)
        if code:
            print('Stopped after technical process failure; no retry.',flush=True)
            break
    for name in SOURCES:
        p=root/'source_snapshot'/name;p.parent.mkdir(parents=True,exist_ok=True);p.write_bytes((ROOT/name).read_bytes())
    print('Joint integration directory:',root,flush=True)


if __name__=='__main__':
    main()

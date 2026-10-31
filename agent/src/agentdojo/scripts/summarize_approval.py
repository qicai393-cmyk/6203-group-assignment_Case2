"""Descriptive audit summary. Does not relabel AgentDojo security as TCR."""
import argparse
import json
from pathlib import Path


def summarize(path):
    events = [json.loads(line) for line in Path(path).read_text().splitlines() if line.strip()]
    totals = {}
    for component in ['agent', 'policy', 'reviewer']:
        rows = [e for e in events if e['event'] == 'llm_usage' and e['component'] == component]
        totals[component] = {'logged_responses': len(rows),
            'input_tokens': sum(e['input_tokens'] or 0 for e in rows),
            'output_tokens': sum(e['output_tokens'] or 0 for e in rows),
            'missing_usage_responses': sum(not e['usage_available'] for e in rows),
            'cost_usd': sum(e['cost_usd'] for e in rows) if rows and all(e['cost_usd'] is not None for e in rows) else None}
    tasks = [e for e in events if e['event'] == 'task_end']
    return {'cost_components': totals, 'completed_trajectories': len(tasks),
        'utility_successes': sum(e['utility'] for e in tasks),
        'escalations_all_completed_trajectories': sum(e['escalations_used'] for e in tasks),
        'escalations_utility_successes': sum(e['escalations_used'] for e in tasks if e['utility']),
        'strict_tcr': None, 'strict_hitl_load': None,
        'metric_note': 'Complete unauthorized-action adjudication is required for strict TCR and safe-success HITL.'}


def main():
    parser = argparse.ArgumentParser()
    parser.add_argument('log')
    parser.add_argument('--output')
    args = parser.parse_args()
    result = json.dumps(summarize(args.log), indent=2)
    if args.output:
        Path(args.output).write_text(result + '\n')
    else:
        print(result)


if __name__ == '__main__':
    main()

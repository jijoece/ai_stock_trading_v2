#!/usr/bin/env python3
"""Read and refresh feature checkpoints directly. Never invoke run_feature.py or Claude."""
import argparse
from datetime import datetime
import json
import math
from pathlib import Path
import re
import sys
import time

SLUG = re.compile(r'^[a-z0-9][a-z0-9-]{0,63}$')


def load_object(path):
    value = json.loads(path.read_text(encoding='utf-8'))
    if not isinstance(value, dict):
        raise ValueError(f'{path.name} must contain a JSON object')
    return value


def text(value, limit=160):
    return str(value or '').replace('\n', ' ').replace('|', '\\|')[:limit]


def worker_note(directory, key, saved):
    if not SLUG.fullmatch(key):
        return {}
    attempt = saved.get('worker', {}).get('attempt')
    if not isinstance(attempt, int) or attempt < 1:
        return {}
    path = directory / 'scratchpad' / 'workers' / key / f'attempt-{attempt:03}.json'
    try:
        note = load_object(path)
        return note if note.get('task_id') == key and note.get('attempt') == attempt else {}
    except (OSError, ValueError):
        return {}


def render(directory):
    state = load_object(directory / 'state.json')
    manifest_path = directory / 'manifest.json'
    manifest = load_object(manifest_path) if manifest_path.exists() else {}
    definitions = manifest.get('tasks', [])
    if not isinstance(definitions, list):
        raise ValueError('manifest.json tasks must be an array')
    definitions = {item['id']: item for item in definitions if isinstance(item, dict) and isinstance(item.get('id'), str)}
    saved_tasks = state.get('tasks', {})
    if not isinstance(saved_tasks, dict):
        raise ValueError('state.json tasks must be an object')
    keys = list(dict.fromkeys(list(definitions) + list(saved_tasks)))
    complete = sum(isinstance(saved_tasks.get(key), dict) and saved_tasks[key].get('status') == 'complete' for key in keys)
    charged = 0.0
    for call in state.get('calls', []):
        if isinstance(call, dict):
            cost = call.get('charged_usd', call.get('total_cost_usd', 0))
            if isinstance(cost, (int, float)) and math.isfinite(cost):
                charged += cost
    updated = datetime.fromtimestamp((directory / 'state.json').stat().st_mtime).astimezone().strftime('%Y-%m-%d %H:%M:%S %Z')
    lines = [f'Validated tasks: {complete}/{len(keys)} | Estimated charged/reserved: ${charged:.2f}',
             f'Last saved run state: {updated}', '',
             '| Task | Saved status | Attempt | Model / effort | Current action | Next action |',
             '| --- | --- | --- | --- | --- | --- |']
    blockers = list(manifest.get('blockers', []))
    evidence = []
    for key in keys:
        saved = saved_tasks.get(key, {})
        if not isinstance(saved, dict):
            saved = {}
        definition = definitions.get(key, {})
        note = worker_note(directory, key, saved)
        status = saved.get('status', 'pending')
        if status == 'pending' and any(saved_tasks.get(dep, {}).get('status') != 'complete' for dep in definition.get('depends_on', [])):
            status = 'waiting on dependencies'
        attempt = saved.get('worker', {}).get('attempt', saved.get('attempts', ''))
        model = definition.get('model', '?')
        effort = definition.get('effort', '?')
        current = note.get('current') or saved.get('summary') or definition.get('title', '')
        next_action = note.get('next_action') or saved.get('handoff', '')
        if status == 'complete':
            current = saved.get('summary') or 'Validation passed'
            next_action = 'None; task validated.'
        elif status == 'validating':
            current, next_action = 'Running independent checks', 'Wait for results.'
        elif status == 'running' and not note:
            next_action = 'Waiting for worker result or checkpoint.'
        lines.append('| ' + ' | '.join(text(item) for item in (key, status, attempt, f'{model} / {effort}', current, next_action)) + ' |')
        blockers.extend(f'{key}: {item}' for item in saved.get('blockers', []))
        if saved.get('error') and status != 'complete':
            blockers.append(f"{key}: {saved['error']}")
        if status != 'complete':
            blockers.extend(f'{key} (worker report): {item}' for item in note.get('blockers', []))
        if note:
            evidence.append(f"{key}: checkpoint {note.get('updated_at', '?')}, sequence {note.get('sequence', '?')}")
            evidence.extend(f'{key}: worker evidence {item}' for item in note.get('evidence', []))
        for check in saved.get('checks', []):
            if isinstance(check, dict):
                evidence.append(f"{key}: check exit {check.get('exit_code', '?')}, log {check.get('log', '?')}")
    if not keys:
        lines.append('Preparation is in progress, or no task manifest has been saved yet.')
    lines += ['', 'Blockers / errors:']
    lines += ['- ' + text(item, 800) for item in blockers] or ['None recorded.']
    lines += ['', 'Checkpoints / validation:']
    lines += ['- ' + text(item, 800) for item in evidence] or ['No worker checkpoints or validation results recorded yet.']
    lines += ['', f'Full logs: {directory / "logs"}',
              'Saved status reflects checkpoints. This monitor does not check process liveness.']
    return '\n'.join(lines)


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--repo', default='.', help='Repository root (default: current directory)')
    parser.add_argument('--feature', required=True, help='The feature name used to launch the run')
    parser.add_argument('--interval', type=float, default=5, help='Seconds between refreshes (default: 5)')
    parser.add_argument('--run-dir', help='Optional explicit directory containing state.json and manifest.json')
    parser.add_argument('--runner', help=argparse.SUPPRESS)  # Accept old invocations; never execute this file.
    parser.add_argument('--once', action='store_true', help='Show status once and exit')
    args = parser.parse_args()
    repo = Path(args.repo).expanduser().resolve()
    if not repo.is_dir():
        parser.error(f'Repository directory does not exist: {repo}')
    if not SLUG.fullmatch(args.feature):
        parser.error('--feature must be the lowercase feature slug used by the runner')
    if not math.isfinite(args.interval) or args.interval <= 0:
        parser.error('--interval must be a positive finite number')
    directory = Path(args.run_dir).expanduser().resolve() if args.run_dir else repo / '.feature-runs' / args.feature
    last_status = ''
    try:
        while True:
            if sys.stdout.isatty():
                print('\033[2J\033[H', end='')
            timestamp = datetime.now().astimezone().strftime('%Y-%m-%d %H:%M:%S %Z')
            print(f'Feature monitor: {args.feature} | {timestamp}')
            print(f'Repository: {repo}')
            print(f'Refresh: {args.interval:g}s | Ctrl+C stops this monitor only\n', flush=True)
            code = 0
            try:
                last_status = render(directory)
                print(last_status)
            except FileNotFoundError:
                print(f'Waiting for checkpoint files in: {directory}')
                code = 1
            except (OSError, ValueError, TypeError, AttributeError) as error:
                if last_status:
                    print(last_status)
                print(f'\nCannot read the latest checkpoint: {error}. Retrying on the next refresh.')
                code = 1
            sys.stdout.flush()
            if args.once:
                return code
            time.sleep(args.interval)
    except KeyboardInterrupt:
        print('\nMonitoring stopped. The feature run continues independently.')
        return 0


if __name__ == '__main__':
    sys.exit(main())

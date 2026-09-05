"""Execute trusted generated fixtures with pinned gitlab-ci-local, without Docker."""
import argparse
import json
import os
from pathlib import Path
import subprocess
import tempfile

from scenarios import ROOT, SCENARIOS, fixture


def git(root, *args):
    return subprocess.check_output(['git', *args], cwd=root, text=True, stderr=subprocess.DEVNULL).strip()


def run(name, destination):
    executable = ROOT / 'node_modules/.bin/gitlab-ci-local'
    if not executable.is_file():
        raise ValueError('run npm ci --ignore-scripts first; the harness never downloads tools implicitly')
    expected_version = json.loads((ROOT / 'package.json').read_text())['devDependencies']['gitlab-ci-local']
    actual = subprocess.check_output([str(executable), '--version'], text=True).strip()
    if actual != expected_version:
        raise ValueError(f'gitlab-ci-local version mismatch: expected {expected_version}, got {actual}')
    with tempfile.TemporaryDirectory(prefix='generic-ci-local-') as temp:
        root = Path(temp)
        jobs, _ = fixture(root, name)
        # GCL 4.75.1 derives a container CI_PROJECT_DIR whenever image is present,
        # even with --force-shell-executor. Change only runner placement locally.
        import yaml
        for job in jobs.values():
            if isinstance(job, dict) and 'script' in job:
                job.pop('image', None)
        (root / '.gitlab-ci.yml').write_text(yaml.safe_dump(jobs, sort_keys=False))
        git(root, 'init', '-b', 'main'); git(root, 'config', 'user.email', 'fixture@example.test'); git(root, 'config', 'user.name', 'Fixture')
        git(root, 'remote', 'add', 'origin', 'https://fixture.internal/team/consumer.git')
        git(root, 'add', '.'); git(root, 'commit', '-m', 'baseline'); before = git(root, 'rev-parse', 'HEAD')
        (root / 'producer/README').write_text('changed\n'); git(root, 'commit', '-am', 'change producer')
        env = {k: v for k, v in os.environ.items() if not k.startswith(('CI_', 'TOOLKIT_', 'GCL_'))}
        result = subprocess.run([str(executable), '--cwd', '.', '--home', str(root / 'isolated-home'),
            '--force-shell-executor', '--shell-isolation', '--no-artifacts-to-source', '--no-color',
            '--variable', 'CI_PIPELINE_SOURCE=push', '--variable', 'CI_COMMIT_BRANCH=main', '--variable', f'CI_COMMIT_BEFORE_SHA={before}'],
            cwd=root, env=env, text=True, stdout=subprocess.PIPE, stderr=subprocess.STDOUT, timeout=120)
        (destination / (name + '.log')).write_text(result.stdout)
        receipts = []
        for path in (root / '.gitlab-ci-local/artifacts').rglob('receipt.json'):
            record = json.loads(path.read_text())
            if record.get('status') == 'passed':
                receipts.append((record['project'], record['action']))
        expected = [('producer', 'check'), ('producer', 'application')]
        if name != 'selection':
            expected.append(('consumer', 'check'))
        if name == 'failed-gate':
            if result.returncode == 0 or receipts or 'expected-gate-failure' not in result.stdout:
                raise AssertionError('failed check did not block all downstream work')
        else:
            if result.returncode or any(r not in receipts for r in expected):
                raise AssertionError(f'{name} failed or lacked success receipts; inspect {destination / (name + ".log")}')
            if name == 'selection' and any(p == 'consumer' for p, _ in receipts):
                raise AssertionError('unaffected consumer executed')
            if name == 'matrix' and receipts.count(('producer', 'check')) != 2:
                raise AssertionError('not every matrix gate executed')
        return {'scenario': name, 'status': 'passed', 'expected_failure': name == 'failed-gate', 'returncode': result.returncode}


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--scenario', choices=SCENARIOS, action='append', help='Repeat to select scenarios; default: all')
    parser.add_argument('--output', type=Path, default=ROOT / '.test-results/local')
    args = parser.parse_args(); args.output.mkdir(parents=True, exist_ok=True)
    results = []
    for name in args.scenario or SCENARIOS:
        try:
            results.append(run(name, args.output))
            print(name + ': passed')
        except (ValueError, AssertionError, OSError, subprocess.SubprocessError) as error:
            results.append({'scenario': name, 'status': 'failed', 'error': str(error)})
            print(name + ': ' + str(error))
    (args.output / 'results.json').write_text(json.dumps(results, indent=2) + '\n')
    return int(any(r['status'] != 'passed' for r in results))

if __name__ == '__main__':
    raise SystemExit(main())

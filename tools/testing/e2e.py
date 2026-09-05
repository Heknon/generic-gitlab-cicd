"""Rare disposable GitLab + project Runner proof. Requires a Linux Docker host."""
import argparse
import base64
import json
import os
from pathlib import Path
import secrets
import shutil
import subprocess
import tempfile
import time
import urllib.error
import urllib.parse
import urllib.request

from scenarios import ROOT, SCENARIOS, fixture

GITLAB_IMAGE = 'gitlab/gitlab-ce:19.2.1-ce.0'
RUNNER_IMAGE = 'gitlab/gitlab-runner:alpine-v19.2.1'
RUNTIME_BASE = 'python:3.12.11-slim-bookworm'
TERMINAL = {'success', 'failed', 'canceled', 'skipped'}


class Harness:
    def __init__(self, args):
        self.args = args
        self.prefix = 'generic-ci-e2e-' + secrets.token_hex(4)
        self.server = self.prefix + '-gitlab'
        self.runner = self.prefix + '-runner'
        self.volume = self.prefix + '-config'
        self.runtime = 'fixture.internal/generic-ci-e2e:' + self.prefix
        self.token = secrets.token_hex(24)
        self.url = 'http://127.0.0.1:' + str(args.port)
        # Local disposable endpoints must never inherit an outbound proxy.
        self.http = urllib.request.build_opener(urllib.request.ProxyHandler({}))

    def docker(self, *args, **kwargs):
        return subprocess.run(['docker', *args], check=True, text=True, stdout=subprocess.PIPE,
                              stderr=subprocess.PIPE, timeout=kwargs.pop('timeout', 180), **kwargs).stdout.strip()

    def api(self, method, path, data=None, raw=False):
        request = urllib.request.Request(self.url + '/api/v4' + path, method=method,
            data=json.dumps(data).encode() if data is not None else None,
            headers={'PRIVATE-TOKEN': self.token, 'Content-Type': 'application/json'})
        try:
            with self.http.open(request, timeout=30) as response:
                body = response.read()
                return body if raw else json.loads(body) if body else None
        except urllib.error.HTTPError as error:
            # Never emit request headers, tokens, or GitLab response bodies.
            raise RuntimeError(f'GitLab {method} {path} returned HTTP {error.code}') from None

    def wait(self, description, fn, timeout=300):
        deadline = time.monotonic() + timeout
        while time.monotonic() < deadline:
            value = fn()
            if value:
                return value
            time.sleep(2)
        raise TimeoutError(description + ' timed out')

    def start(self):
        if not shutil.which('docker'):
            raise RuntimeError('Docker is required; E2E cannot run in a shell-only environment')
        self.docker('info')
        config = f"""external_url '{self.url}'
nginx['listen_port'] = {self.args.port}
letsencrypt['enable'] = false
prometheus_monitoring['enable'] = false
registry['enable'] = false
gitlab_kas['enable'] = false
puma['worker_processes'] = 2
sidekiq['concurrency'] = 5
"""
        self.docker('run', '-d', '--name', self.server, '--label', 'com.generic-ci.e2e=true', '--publish', f'127.0.0.1:{self.args.port}:{self.args.port}',
                    '--shm-size', '256m', '--env', 'GITLAB_OMNIBUS_CONFIG=' + config, self.args.gitlab_image, timeout=600)
        def ready():
            if self.docker('inspect', '-f', '{{.State.Running}}', self.server) != 'true':
                raise RuntimeError('GitLab container exited during startup')
            return subprocess.run(['docker', 'exec', self.server, 'curl', '-sf', '--noproxy', '*',
                                   self.url + '/-/readiness'], stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL, timeout=30).returncode == 0
        self.wait('GitLab readiness', ready, timeout=1500)
        ruby = """user = User.find_by_username('root')
token = user.personal_access_tokens.build(scopes: [:api], name: 'generic-ci-e2e', expires_at: 1.day.from_now)
token.set_token('%s')
token.save!
""" % self.token
        self.docker('exec', '-i', self.server, 'gitlab-rails', 'runner', '-', input=ruby, timeout=180)
        # Public dependency acquisition is confined to this maintainer test image.
        with tempfile.TemporaryDirectory() as temp:
            context = Path(temp)
            for name in ['pyproject.toml', 'README.md']:
                shutil.copyfile(ROOT / name, context / name)
            shutil.copytree(ROOT / 'generic_ci', context / 'generic_ci', ignore=shutil.ignore_patterns('__pycache__'))
            (context / 'Dockerfile').write_text('ARG BASE\nFROM ${BASE}\nRUN apt-get update && apt-get install -y --no-install-recommends git bash ca-certificates && rm -rf /var/lib/apt/lists/*\nCOPY . /toolkit\nRUN pip install --no-cache-dir /toolkit\n')
            self.docker('build', '--build-arg', 'BASE=' + self.args.runtime_base, '-t', self.runtime, str(context), timeout=600)
        self.docker('volume', 'create', '--label', 'com.generic-ci.e2e=true', self.volume)
        self.docker('run', '-d', '--name', self.runner, '--label', 'com.generic-ci.e2e=true', '--network', 'host',
                    '-v', '/var/run/docker.sock:/var/run/docker.sock', '-v', self.volume + ':/etc/gitlab-runner',
                    self.args.runner_image, timeout=600)
        self.api('PUT', '/application/settings', {'allow_local_requests_from_web_hooks_and_services': True})

    def register(self, project_id):
        registration = self.api('POST', '/user/runners', {'runner_type': 'project_type', 'project_id': project_id,
            'description': self.prefix, 'tag_list': ['generic-ci-fixture'], 'run_untagged': False})
        self.docker('exec', self.runner, 'gitlab-runner', 'register', '--non-interactive', '--url', self.url,
                    '--token', registration['token'], '--executor', 'docker', '--docker-image', self.runtime,
                    '--docker-network-mode', 'host', '--docker-pull-policy', 'if-not-present', '--docker-privileged=false')

    def commit(self, project_id, actions, message, branch='main', start_branch=None):
        body = {'branch': branch, 'commit_message': message, 'actions': actions}
        if start_branch:
            body['start_branch'] = start_branch
        return self.api('POST', f'/projects/{project_id}/repository/commits', body)['id']

    def run_case(self, scenario):
        is_mr = scenario == 'merge-request'
        underlying = 'handoff' if is_mr or scenario == 'manual-gate' else scenario
        event = 'release' if scenario.startswith('release-') else 'merge-request' if is_mr else 'push'
        project = self.api('POST', '/projects', {'name': self.prefix + '-' + scenario, 'visibility': 'private',
                                               'initialize_with_readme': True, 'default_branch': 'main'})
        pid = project['id']; self.register(pid)
        with tempfile.TemporaryDirectory() as temp:
            root = Path(temp); jobs, _ = fixture(root, underlying, self.runtime, event)
            # Supplemental native gate checks real manual play semantics independently
            # of deployment tooling; no Buildah, Helm or production endpoint is used.
            if scenario == 'manual-gate':
                jobs['fixture-approval'] = {'stage': 'delivery', 'image': self.runtime, 'tags': ['generic-ci-fixture'],
                    'needs': ['push:consumer:consume'], 'script': ['echo manual-proof'],
                    'rules': [{'if': '$CI_COMMIT_BRANCH', 'when': 'manual', 'allow_failure': False}]}
                import yaml
                (root / '.gitlab-ci.yml').write_text(yaml.safe_dump(jobs, sort_keys=False))
            actions = [{'action': 'create', 'file_path': p.relative_to(root).as_posix(),
                        'encoding': 'base64', 'content': base64.b64encode(p.read_bytes()).decode()}
                       for p in sorted(root.rglob('*')) if p.is_file()]
            self.commit(pid, actions, 'fixture baseline [skip ci]')
        sha = self.commit(pid, [{'action': 'update', 'file_path': 'producer/README', 'content': 'changed\n'}],
                          'exercise ' + scenario, branch='candidate' if is_mr else 'main', start_branch='main' if is_mr else None)
        if scenario.startswith('release-'):
            self.api('POST', f'/projects/{pid}/protected_tags', {'name': 'fixture/*', 'create_access_level': 40})
            self.api('POST', f'/projects/{pid}/repository/tags', {'tag_name': 'fixture/v1.0.0', 'ref': sha})
        if is_mr:
            self.api('POST', f'/projects/{pid}/merge_requests', {'source_branch': 'candidate', 'target_branch': 'main', 'title': 'E2E candidate'})
        source = 'merge_request_event' if is_mr else 'push'
        pipeline = self.wait('pipeline creation', lambda: next((p for p in self.api('GET', f'/projects/{pid}/pipelines?sha={sha}')
                                                              if p['source'] == source and (p['ref'] == 'fixture/v1.0.0' if scenario.startswith('release-') else True)), None))
        pipeline_id = pipeline['id']
        def observe():
            value = self.api('GET', f'/projects/{pid}/pipelines/{pipeline_id}')
            return value if value['status'] in TERMINAL | {'manual'} else None
        outcome = self.wait('pipeline completion', observe, timeout=600)
        if scenario == 'manual-gate':
            assert outcome['status'] == 'manual', 'pipeline must block for approval'
            rows = self.api('GET', f'/projects/{pid}/pipelines/{pipeline_id}/jobs?per_page=100')
            gate = next(j for j in rows if j['name'] == 'fixture-approval')
            self.api('POST', f'/projects/{pid}/jobs/{gate["id"]}/play')
            outcome = self.wait('manual approval', lambda: (p if (p := observe()) and p['status'] in TERMINAL else None), timeout=300)
        rows = self.api('GET', f'/projects/{pid}/pipelines/{pipeline_id}/jobs?per_page=100')
        statuses = {j['name']: j['status'] for j in rows}
        evidence = {'scenario': scenario, 'commit': sha, 'pipeline': pipeline_id,
                    'pipeline_status': outcome['status'], 'jobs': statuses, 'gitlab_image': self.args.gitlab_image}
        (self.args.output / (scenario + '.json')).write_text(json.dumps(evidence, indent=2) + '\n')
        for job in rows:
            if job.get('started_at') is None:
                continue
            trace = self.api('GET', f'/projects/{pid}/jobs/{job["id"]}/trace', raw=True)
            (self.args.output / (scenario + '-' + str(job['id']) + '.log')).write_bytes(trace)
        if underlying in {'failed-gate', 'piped-failure', 'release-failure'}:
            assert outcome['status'] == 'failed' and statuses[event + ':producer:verify'] == 'failed', evidence
            assert statuses[event + ':producer:build'] == 'skipped' and statuses[event + ':consumer:consume'] == 'skipped', evidence
            if underlying == 'release-failure':
                assert statuses['release:consumer:release'] == 'skipped', evidence
        else:
            assert outcome['status'] == 'success', evidence
            assert all(status == 'success' for status in statuses.values()), evidence
            if underlying != 'selection':
                consumer = next(j for j in rows if j['name'] == event + ':consumer:consume')
                path = urllib.parse.quote('.ci-out/' + event + ':consumer:consume/receipt.json', safe='/')
                receipt = json.loads(self.api('GET', f'/projects/{pid}/jobs/{consumer["id"]}/artifacts/{path}', raw=True))
                assert receipt['status'] == 'passed' and receipt['commit'] == sha, receipt
                if underlying == 'release-completion':
                    assert statuses['release:producer:release'] == statuses['release:consumer:release'] == 'success', evidence
            else:
                assert event + ':consumer:consume' not in statuses, evidence
        return evidence

    def cleanup(self):
        if not shutil.which('docker'):
            return
        commands = [['rm', '-fv', name] for name in [self.runner, self.server]]
        commands.extend([['volume', 'rm', self.volume], ['image', 'rm', self.runtime]])
        for command in commands:
            try:
                subprocess.run(['docker', *command], capture_output=True, timeout=60)
            except (OSError, subprocess.TimeoutExpired):
                print('Cleanup incomplete; remove resources with prefix ' + self.prefix, flush=True)


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--scenario', choices=(*SCENARIOS, 'merge-request', 'manual-gate'), action='append')
    parser.add_argument('--gitlab-image', default=GITLAB_IMAGE)
    parser.add_argument('--runner-image', default=RUNNER_IMAGE)
    parser.add_argument('--runtime-base', default=RUNTIME_BASE)
    parser.add_argument('--port', type=int, default=8929)
    parser.add_argument('--output', type=Path, default=ROOT / '.test-results/e2e')
    args = parser.parse_args(); args.output.mkdir(parents=True, exist_ok=True)
    for value in [args.gitlab_image, args.runner_image, args.runtime_base]:
        if ':' not in value or value.endswith((':latest', ':alpine')):
            parser.error('use an explicit version or digest for every test image')
    harness = Harness(args); results = []
    try:
        harness.start()
        for name in args.scenario or (*SCENARIOS, 'merge-request', 'manual-gate'):
            print('E2E: ' + name, flush=True)
            try:
                results.append({**harness.run_case(name), 'status': 'passed'})
            except (AssertionError, RuntimeError, OSError, TimeoutError, StopIteration) as error:
                results.append({'scenario': name, 'status': 'failed', 'error': str(error)})
    except (RuntimeError, OSError, subprocess.SubprocessError) as error:
        # Subprocess commands can contain a disposable runner token, including
        # timeout exceptions. Keep all command arguments out of saved evidence.
        message = type(error).__name__ if isinstance(error, subprocess.SubprocessError) else str(error)
        results.append({'scenario': 'infrastructure', 'status': 'failed', 'error': message})
    finally:
        (args.output / 'results.json').write_text(json.dumps(results, indent=2) + '\n')
        harness.cleanup()
    return int(not results or any(r['status'] != 'passed' for r in results))

if __name__ == '__main__':
    raise SystemExit(main())

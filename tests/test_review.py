"""Regression cases from the repository review, using real shells and Git."""
import contextlib
import io
import json
import os
from pathlib import Path
import subprocess
import sys
import tempfile
import types
import unittest
from unittest.mock import patch

from generic_ci.compiler import digest, variants
from generic_ci.runtime import commands, bundle_dependencies, identity, write_json, receipt_path
from generic_ci.workflows.compiler import compile_pipeline
from generic_ci.workflows.models import Pipeline
from generic_ci.workflows.runtime import make_plan, run_job
from generic_ci.workflows.helm import require_forward_commit
from test_workflows import infra


def compile_projects(projects, deployments=None):
    return compile_pipeline(Pipeline.model_validate({'projects': projects, 'deployments': deployments or {}}), infra())


def app(path='.', release=None):
    result = {'path': path, 'container': {}, 'workflows': {'merge-request': {'build': ['container']}}}
    if release:
        result['release'] = {'tag': release, 'version': {'file': 'version.toml', 'field': 'version'}}
        result['workflows']['release'] = {'build': ['container']}
    return result


def deployment(owners, event='merge-request', update='complete'):
    return {'target': 'test', 'chart': {'path': 'chart'}, 'update': update,
            'images': [{'from': name + '.build-image', 'set': {'tag': name + '.tag'}} for name in owners],
            'workflows': {event: {'when': 'automatic'}}}


class ReviewRegressions(unittest.TestCase):
    def test_empty_tags_are_omitted(self):
        jobs, _ = compile_projects({'app': app()})
        for job in jobs.values():
            if isinstance(job, dict) and 'script' in job:
                self.assertNotIn('tags', job)

    def test_reject_generated_name_collisions(self):
        for name in ['version', 'build-image', 'publish', 'create-release']:
            with self.subTest(name=name):
                p = app(release='v{version}')
                p['checks'] = {name: {'script': ['true']}}
                p['workflows']['push'] = {'checks': [name], 'build': ['container']}
                p['release']['create'] = {}
                if name == 'publish':
                    p['workflows']['release']['checks'] = [name]
                with self.assertRaisesRegex(ValueError, 'collision'):
                    compile_projects({'app': p})

    def test_reject_empty_matrix(self):
        with self.assertRaisesRegex(ValueError, 'nonempty'):
            variants({'parallel': {'matrix': []}})

    def plan(self, data, root, changed='a/code.py', tag=''):
        with patch.dict(os.environ, {'CI_PIPELINE_SOURCE': 'push' if tag else 'merge_request_event',
                                   'CI_COMMIT_TAG': tag, 'CI_COMMIT_BEFORE_SHA': 'a' * 40,
                                   'CI_COMMIT_SHA': 'b' * 40, 'CI_PIPELINE_ID': '1'}, clear=True), \
             patch('generic_ci.workflows.runtime.git', return_value=changed), contextlib.redirect_stdout(io.StringIO()):
            make_plan(data, root, digest(data))
        return json.loads((root / '.ci-out/plan.json').read_text())

    def test_overlapping_deployments_are_order_independent(self):
        projects = {n: app(n) for n in 'abc'}
        entries = [('bc', deployment(['b', 'c'])), ('ab', deployment(['a', 'b']))]
        for order in [entries, entries[::-1]]:
            _, data = compile_projects(projects, dict(order))
            with tempfile.TemporaryDirectory() as temp:
                self.assertEqual(self.plan(data, Path(temp))['selected'], ['a', 'b', 'c'])

    def test_contextual_check_artifact_producer_executes(self):
        projects = {'a': app('a'), 'qa': {'path': 'qa',
            'checks': {'make': {'script': ['echo proof > payload'], 'outputs': ['payload']},
                       'smoke': {'script': ['test "$(cat payload)" = proof'], 'needs': ['make']}},
            'workflows': {'merge-request': {'checks': ['make']}}}}
        dep = deployment(['a']); dep['before'] = {'checks': ['qa.smoke']}
        _, data = compile_projects(projects, {'preview': dep}); expected = digest(data)
        with tempfile.TemporaryDirectory() as temp, patch.dict(os.environ, {'CI_COMMIT_SHA': 'b' * 40, 'CI_PIPELINE_ID': '1'}, clear=True):
            root = Path(temp); (root / 'qa').mkdir()
            self.assertEqual(self.plan(data, root)['selected'], ['a', 'qa'])
            write_json(receipt_path(root, 'merge-request:a:build-image'),
                       {**identity(expected), 'status': 'passed', 'action': 'container', 'project': 'a', 'files': []})
            run_job(data, 'merge-request:qa:make', root, expected)
            (root / 'qa/payload').unlink()
            run_job(data, 'merge-request:preview-before:qa:smoke', root, expected)
            self.assertTrue(receipt_path(root, 'merge-request:preview-before:qa:smoke').is_file())

    def test_independent_tags_require_partial_deployment(self):
        projects = {n: app(n, n + '/v{version}') for n in 'ab'}
        with self.assertRaisesRegex(ValueError, 'shared release tag'):
            compile_projects(projects, {'both': deployment(['a', 'b'], 'release')})
        _, data = compile_projects(projects, {'both': deployment(['a', 'b'], 'release', 'partial')})
        with tempfile.TemporaryDirectory() as temp:
            self.assertEqual(self.plan(data, Path(temp), tag='a/v1.0.0')['selected'], ['a'])
        projects['b']['release']['tag'] = 'a/v{version}'
        compile_projects(projects, {'both': deployment(['a', 'b'], 'release')})

    def test_shell_state_and_failure_stop(self):
        with tempfile.TemporaryDirectory() as temp:
            root = Path(temp)
            commands(['export PROOF=value', 'mkdir child', 'cd child', 'echo "$PROOF" > proof'], root, 'sh')
            self.assertEqual((root / 'child/proof').read_text(), 'value\n')
            with self.assertRaises(subprocess.CalledProcessError):
                commands(['false', 'touch unexpected'], root, 'sh')
            self.assertFalse((root / 'unexpected').exists())

    def test_shallow_forward_update_recovers_but_rollback_fails(self):
        with tempfile.TemporaryDirectory() as temp:
            source = Path(temp) / 'source'; source.mkdir()
            def git(*args):
                return subprocess.check_output(['git', *args], cwd=source, text=True, stderr=subprocess.DEVNULL).strip()
            git('init'); git('config', 'user.email', 'test@example.test'); git('config', 'user.name', 'Test')
            (source / 'file').write_text('old'); git('add', '.'); git('commit', '-m', 'old'); old = git('rev-parse', 'HEAD')
            (source / 'file').write_text('new'); git('commit', '-am', 'new'); new = git('rev-parse', 'HEAD')
            clone = Path(temp) / 'clone'
            subprocess.run(['git', 'clone', '--depth=1', source.as_uri(), str(clone)], check=True, capture_output=True)
            require_forward_commit(clone, old, new)
            with self.assertRaisesRegex(ValueError, 'stale/nonlinear'):
                require_forward_commit(clone, new, old)
            with self.assertRaisesRegex(ValueError, 'cannot verify'):
                require_forward_commit(clone, 'f' * 40, new)

    def test_bundle_preserves_git_subdirectory(self):
        with tempfile.TemporaryDirectory() as temp:
            root = Path(temp); (root / 'pyproject.toml').write_text('[project]\nname="app"\n')
            dist = types.SimpleNamespace(metadata={'Name': 'sdk'}, version='1.0', read_text=lambda _: json.dumps({
                'url': 'https://registry.internal/mono.git', 'vcs_info': {'commit_id': 'a' * 40}, 'subdirectory': 'packages/sdk'}))
            def execute(args, **kwargs):
                output = io.StringIO()
                with patch('importlib.metadata.distributions', return_value=[dist]), \
                     patch.object(sys, 'argv', ['-c', args[-1]]), contextlib.redirect_stdout(output):
                    exec(args[2], {})
                return output.getvalue()
            with patch('generic_ci.runtime.subprocess.check_output', side_effect=execute), patch('generic_ci.runtime.subprocess.run'):
                bundle_dependencies({'interpreter': sys.executable}, root, root, ['registry.internal'])
            self.assertIn('#subdirectory=packages/sdk', (root / 'resolved-requirements.txt').read_text())

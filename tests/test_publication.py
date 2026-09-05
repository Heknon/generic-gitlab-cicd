import contextlib
import json
import os
from pathlib import Path
import subprocess
import sys
import tempfile
import unittest
from unittest.mock import patch

from generic_ci.compiler import digest
from generic_ci.workflows.compiler import compile_pipeline
from generic_ci.workflows.models import Pipeline
from generic_ci.workflows.publication import (snapshot_version, release_channel, destination,
                                            rewrite_snapshot, check_development_context)
from generic_ci.workflows.runtime import make_plan, run_job, publish
from test_workflows import infra


def config(node=False):
    return {'projects': {'sdk': {
        **({'node': {'package-manager': 'npm'}} if node else {'python': {}}),
        'package': {'index': 'release', 'preview': {'registry': 'https://registry.internal/preview'} if node else {'index': 'preview'}},
        'checks': {'unit': {'script': ['true']}},
        'workflows': {'push': {'checks': ['unit'], 'publish': {'channel': 'development'}}},
    }}}


def python_manifest():
    return '''[project]
name = "sdk"
version = "1.5.0"
[[tool.uv.index]]
name = "release"
url = "https://registry.internal/release/simple"
publish-url = "https://registry.internal/release"
[[tool.uv.index]]
name = "preview"
url = "https://registry.internal/preview/simple"
publish-url = "https://registry.internal/preview"
explicit = true
'''


class PublicationTests(unittest.TestCase):
    def test_build_is_inferred_and_gated(self):
        _, data = compile_pipeline(Pipeline.model_validate(config()), infra())
        self.assertEqual(data['nodes']['push:sdk:build-package']['needs'], ['push:sdk:unit'])
        self.assertIn('push:sdk:build-package', data['nodes']['push:sdk:publish']['needs'])
        bad = config(); bad['projects']['sdk']['package'].pop('preview')
        _, fallback = compile_pipeline(Pipeline.model_validate(bad), infra())
        self.assertIn('push:sdk:publish', fallback['nodes'])

    def test_release_and_development_events_are_distinct(self):
        c = config(); p = c['projects']['sdk']; p['release'] = {'tag': 'v{version}'}
        p['workflows'] = {'release': {'publish': True}}
        _, data = compile_pipeline(Pipeline.model_validate(c), infra())
        self.assertIn('release:sdk:build-package', data['nodes'])
        p['workflows']['release']['publish'] = {'channel': 'development'}
        with self.assertRaisesRegex(ValueError, 'release tags'):
            compile_pipeline(Pipeline.model_validate(c), infra())
        p['workflows'] = {'push': {'publish': {'channel': 'auto'}}}
        with self.assertRaisesRegex(ValueError, 'release workflow'):
            compile_pipeline(Pipeline.model_validate(c), infra())

    def test_version_identity_and_channels(self):
        with patch.dict(os.environ, {'CI_PIPELINE_ID': '4821'}):
            self.assertEqual(snapshot_version('1.5.0rc1'), '1.5.0.dev4821')
            self.assertEqual(snapshot_version('1.5.0-beta.1', True), '1.5.0-dev.4821')
            with self.assertRaises(ValueError): snapshot_version('1.5.0.post1')
        for value, node, channel in [('1.5.0', False, 'latest'), ('1.5.0b1', False, 'beta'), ('1.5.0rc1', False, 'rc'), ('1.5.0-beta.1', True, 'beta')]:
            self.assertEqual(release_channel(value, node), channel)
        for value, node, expected in [('1.5.0.dev1', False, 'dev'), ('1.5.0-dev.1', True, 'dev'), ('1.5.0-canary.1', True, 'canary'), ('1.5.0-latest', True, 'latest')]:
            self.assertEqual(release_channel(value, node), expected)

    def test_python_index_policy_is_user_owned(self):
        project = Pipeline.model_validate(config()).model_dump()['projects']['sdk']
        with tempfile.TemporaryDirectory() as tmp:
            directory = Path(tmp); file = directory / 'pyproject.toml'; file.write_text(python_manifest())
            self.assertEqual(destination(project, directory, ['registry.internal'], True)['index'], 'preview')
            file.write_text(python_manifest().replace('explicit = true', 'explicit = false'))
            self.assertEqual(destination(project, directory, ['registry.internal'], True)['index'], 'preview')
            file.write_text(python_manifest().replace('https://registry.internal/preview"', 'https://registry.internal/release/"'))
            self.assertEqual(destination(project, directory, ['registry.internal'], True)['url'], 'https://registry.internal/release')
            project['package']['preview'] = None
            self.assertEqual(destination(project, directory, ['registry.internal'], True)['index'], 'release')

    def test_node_registry_policy_is_user_owned(self):
        project = Pipeline.model_validate(config(True)).model_dump()['projects']['sdk']
        with tempfile.TemporaryDirectory() as tmp:
            directory = Path(tmp)
            (directory / 'package.json').write_text(json.dumps({'name': 'sdk', 'version': '1.5.0', 'publishConfig': {'registry': 'https://registry.internal/preview/'}}))
            self.assertEqual(destination(project, directory, ['registry.internal'], True)['url'], 'https://registry.internal/preview')
            project['package']['preview'] = None
            self.assertEqual(destination(project, directory, ['registry.internal'], True)['url'], 'https://registry.internal/preview')

    def test_development_context_does_not_police_forks_or_candidates(self):
        plan = {'event': 'merge-request', 'candidates': []}
        with patch.dict(os.environ, {'CI_PROJECT_ID': '1', 'CI_MERGE_REQUEST_SOURCE_PROJECT_ID': '2', 'CI_MERGE_REQUEST_TARGET_PROJECT_ID': '1'}, clear=True):
            check_development_context(plan)
            os.environ['CI_MERGE_REQUEST_SOURCE_PROJECT_ID'] = '1'
            check_development_context(plan)
            check_development_context({**plan, 'candidates': [{}]})

    def test_python_snapshot_copy_rewrite(self):
        with tempfile.TemporaryDirectory() as tmp, patch.dict(os.environ, {'CI_PIPELINE_ID': '4821'}):
            file = Path(tmp) / 'pyproject.toml'; file.write_text(python_manifest())
            rewrite_snapshot(Path(tmp), False, {'url': 'https://registry.internal/preview'})
            self.assertIn('version = "1.5.0.dev4821"', file.read_text())
            self.assertIn('explicit = true', file.read_text())

    def test_real_python_snapshot_archives(self):
        _, data = compile_pipeline(Pipeline.model_validate(config()), infra())
        expected = digest(data)
        run = subprocess.run
        def build_or_check(args, **kwargs):
            if args[:4] == [sys.executable, '-m', 'twine', 'check']:
                return subprocess.CompletedProcess(args, 0)
            return run(args, **kwargs)
        env = {'PATH': os.environ['PATH'], 'CI_PIPELINE_SOURCE': 'push', 'CI_PIPELINE_ID': '4821',
               'CI_COMMIT_SHA': 'a'*40, 'UV_OFFLINE': 'true', 'UV_NO_BUILD_ISOLATION': 'true', 'UV_PYTHON': sys.executable}
        with tempfile.TemporaryDirectory() as tmp, patch.dict(os.environ, env, clear=True):
            root = Path(tmp)
            original = python_manifest() + '\n[build-system]\nrequires = ["setuptools"]\nbuild-backend = "setuptools.build_meta"\n'
            (root / 'pyproject.toml').write_text(original)
            (root / 'sdk.py').write_text('VALUE = 1\n')
            with patch('generic_ci.workflows.runtime.environment', return_value=contextlib.nullcontext({})), patch('generic_ci.workflows.runtime.subprocess.run', side_effect=build_or_check):
                make_plan(data, root, expected)
                run_job(data, 'push:sdk:unit', root, expected)
                run_job(data, 'push:sdk:build-package', root, expected)
            receipt = json.loads((root / '.ci-out/push:sdk:build-package/receipt.json').read_text())
            self.assertEqual(receipt['package']['version'], '1.5.0.dev4821')
            self.assertEqual(len(receipt['files']), 2)
            self.assertEqual((root / 'pyproject.toml').read_text(), original)
            with patch('generic_ci.workflows.runtime.subprocess.run') as upload, patch('generic_ci.workflows.runtime.api') as api:
                run_job(data, 'push:sdk:publish', root, expected)
                args = upload.call_args.args[0]
                self.assertEqual(args[args.index('--publish-url')+1], 'https://registry.internal/preview')
                self.assertEqual(args[args.index('--check-url')+1], 'https://registry.internal/preview/simple')
                api.assert_not_called()

    def test_real_node_package_and_mocked_preview_upload(self):
        self.run_node_snapshot(False)

    def test_failed_build_preserves_original_manifest(self):
        self.run_node_snapshot(True)

    def run_node_snapshot(self, fail):
        _, data = compile_pipeline(Pipeline.model_validate(config(True)), infra())
        expected = digest(data)
        with tempfile.TemporaryDirectory() as tmp, patch.dict(os.environ, {'PATH': os.environ['PATH'], 'CI_PIPELINE_SOURCE': 'push', 'CI_PIPELINE_ID': '4821', 'CI_COMMIT_SHA': 'a'*40, 'npm_config_offline': 'true'}, clear=True):
            root = Path(tmp)
            original = json.dumps({'name': 'sdk', 'version': '1.5.0', 'publishConfig': {'registry': 'https://registry.internal/release', 'tag': 'latest'}})
            (root / 'package.json').write_text(original)
            # Preparation is covered separately by real frozen-install fixtures.
            with patch('generic_ci.workflows.runtime.environment', return_value=contextlib.nullcontext({})):
                make_plan(data, root, expected)
                run_job(data, 'push:sdk:unit', root, expected)
                if fail:
                    with patch('generic_ci.workflows.runtime.package_build', side_effect=RuntimeError('pack failed')):
                        with self.assertRaisesRegex(RuntimeError, 'pack failed'):
                            run_job(data, 'push:sdk:build-package', root, expected)
                else:
                    run_job(data, 'push:sdk:build-package', root, expected)
                    receipt = json.loads((root / '.ci-out/push:sdk:build-package/receipt.json').read_text())
                    self.assertEqual(receipt['package']['version'], '1.5.0-dev.4821')
                    with patch('generic_ci.workflows.runtime.subprocess.run') as upload, patch('generic_ci.workflows.runtime.api') as api:
                        run_job(data, 'push:sdk:publish', root, expected)
                        args = upload.call_args.args[0]
                        self.assertEqual(args[args.index('--registry')+1], 'https://registry.internal/preview')
                        self.assertEqual(args[args.index('--tag')+1], 'dev')
                        api.assert_not_called()
                    receipt['publication']['destination']['url'] = 'https://registry.internal/release'
                    with self.assertRaisesRegex(ValueError, 'destination differs'):
                        publish(data, data['nodes']['push:sdk:publish'], data['pipeline']['projects']['sdk'], root, [receipt], {'event': 'push', 'candidates': []})
            self.assertEqual((root / 'package.json').read_text(), original)

"""Regression cases derived from failing consumer journeys in the 0.3.4 audit."""
import base64
import contextlib
import copy
import io
import json
import os
from pathlib import Path
import subprocess
import tempfile
import unittest
from unittest.mock import patch

from generic_ci.cli import main
from generic_ci.compiler import digest
from generic_ci.runtime import commands, load_config
from generic_ci.workflows.compiler import compile_pipeline
from generic_ci.workflows.helm import deploy
from generic_ci.workflows.models import Pipeline, Platform
from generic_ci.workflows.runtime import make_plan, run_job, validate_version
from generic_ci.workflows.selection import select, matches_path


def platform():
    return {'images': {'python': 'registry.internal/python', 'helm': 'registry.internal/helm'},
            'container-builder': {'image': 'registry.internal/buildah'},
            'registries': {'containers': 'registry.internal/apps', 'previews': 'registry.internal/previews'},
            'allowed-hosts': ['registry.internal'],
            'targets': {'preview': {'namespace': 'preview', 'production': False},
                        'production': {'namespace': 'prod', 'production': True}}}


def service(name):
    return {'path': name, 'container': {}, 'release': {'tag': 'v{version}', 'version': {'file': 'version.toml', 'field': 'version'}},
            'checks': {'test': {'script': ['true']}}, 'workflows': {e: {'checks': ['test'], 'build': ['container']} for e in ['release', 'merge-request']}}


def deployment(names, target='preview', event='merge-request'):
    return {'target': target, 'chart': {'path': 'deploy/chart'}, 'values': ['deploy/values.yaml'],
            'images': [{'from': n + '.build-image', 'set': {'repository': n + '.repository', 'tag': n + '.tag'}} for n in names],
            'workflows': {event: {'when': 'manual'}}}


class DeliveryCoherence(unittest.TestCase):
    def compile(self, projects, deployments=None, infra=None):
        return compile_pipeline(Pipeline.model_validate({'projects': projects, 'deployments': deployments or {}}), Platform.model_validate(infra or platform()))

    def test_piped_failure_blocks_later_commands(self):
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            with self.assertRaises(subprocess.CalledProcessError):
                commands(['false | tee log', 'touch should-not-exist'], root, 'sh')
            self.assertFalse((root / 'should-not-exist').exists())

    def test_release_deployment_waits_for_package_and_coordinated_release(self):
        sdk, api = service('sdk'), service('api')
        sdk['python'] = {}; sdk['package'] = {'index': 'internal'}
        sdk['workflows']['release']['publish'] = True
        api['release']['needs'] = ['sdk']
        jobs, data = self.compile({'sdk': sdk, 'api': api}, {'api': deployment(['api'], 'production', 'release')})
        def ancestors(key):
            deps = set(data['nodes'][key]['needs'])
            return deps | {d for k in deps for d in ancestors(k)}
        for key in ['release:approve:api', 'release:deploy:api']:
            self.assertIn('release:sdk:publish', ancestors(key))
        self.assertNotIn('release:api:publish', jobs)
        self.assertIn('release:api:release', jobs)
        api['python'] = {}; api['package'] = {'index': 'internal'}; api['workflows']['release']['publish'] = True
        _, data = self.compile({'sdk': sdk, 'api': api}, {'api': deployment(['api'], 'production', 'release')})
        self.assertIn('release:sdk:publish', ancestors('release:api:publish'))

    def test_release_completion_does_not_require_api_token(self):
        _, data = self.compile({'app': service('app')})
        from generic_ci.workflows.runtime import finalize_release
        with tempfile.TemporaryDirectory() as tmp, patch.dict(os.environ, {'CI_COMMIT_REF_PROTECTED': 'true', 'CI_COMMIT_TAG': 'v1.0.0'}, clear=True):
            root = Path(tmp); (root / 'app').mkdir(); (root / 'app/version.toml').write_text('version="1.0.0"')
            with patch('generic_ci.workflows.runtime.api') as api:
                result = finalize_release(data, data['pipeline']['projects']['app'], root, {'event': 'release', 'candidates': []})
                self.assertEqual(result['tag'], 'v1.0.0'); api.assert_not_called()
                data['pipeline']['projects']['app']['release']['gitlab'] = True
                api.return_value = {'tag_name': 'v1.0.0'}
                finalize_release(data, data['pipeline']['projects']['app'], root, {'event': 'release', 'candidates': []})
                api.assert_called_once()

    def test_preview_rejects_production_in_compiler_and_runtime(self):
        with self.assertRaisesRegex(ValueError, 'nonproduction'):
            self.compile({'app': service('app')}, {'app': deployment(['app'], 'production')})
        _, data = self.compile({'app': service('app')}, {'app': deployment(['app'])})
        data['platform']['targets']['preview']['production'] = True
        with tempfile.TemporaryDirectory() as tmp, patch('generic_ci.workflows.helm.subprocess.run') as execute:
            with self.assertRaisesRegex(ValueError, 'nonproduction'):
                deploy(data, data['nodes']['merge-request:deploy:app'], Path(tmp), Path(tmp), [], {})
            execute.assert_not_called()

    def test_deployment_changes_select_images_without_forcing_version_bump(self):
        jobs, data = self.compile({'api': service('api'), 'other': service('other')}, {'api': deployment(['api'])})
        for changed in ['deploy/values.yaml', 'deploy/chart/templates/deployment.yaml']:
            result = select(data, 'merge-request', [changed])
            self.assertEqual(result['selected'], ['api']); self.assertEqual(result['direct'], [])
            self.assertEqual(result['deployments'], ['api'])
            for key in ['merge-request:api:build-image', 'merge-request:approve:api', 'merge-request:stop:api']:
                self.assertTrue(any(matches_path(changed, p) for p in jobs[key]['rules'][-1]['changes']['paths']))
        self.assertFalse(any(matches_path('api/code.py', p) for p in jobs['merge-request:other:test']['rules'][-1]['changes']['paths']))

    def test_dependency_filtering_keeps_producers_and_downstream_projects(self):
        a,b,c = [service(n) for n in 'abc']
        a['build'] = {'script': ['true'], 'outputs': ['generated']}; a['workflows']['merge-request']['build'].append('application'); a['workflows']['release']['build'].append('application')
        b['checks']['test']['needs'] = ['a.build']; c['depends-on'] = ['b']
        jobs,data = self.compile({'a': a, 'b': b, 'c': c})
        result = select(data, 'merge-request', ['b/code.py'])
        self.assertEqual(result['selected'], ['a','b','c'])
        for key in ['merge-request:a:build', 'merge-request:a:test', 'merge-request:c:test']:
            self.assertTrue(any(matches_path('b/code.py', p) for p in jobs[key]['rules'][-1]['changes']['paths']))

    def test_released_version_can_be_retested_without_fetching_tags(self):
        for event, bump in [('schedule', True), ('push', True), ('manual', True), ('merge-request', False)]:
            with self.subTest(event=event), tempfile.TemporaryDirectory() as tmp:
                project = service('app'); project['release']['require-bump'] = bump
                _,data = self.compile({'app': project}); root=Path(tmp); (root/'app').mkdir(); (root/'app/version.toml').write_text('version="1.0.0"')
                with patch('generic_ci.workflows.runtime.subprocess.run') as run:
                    self.assertEqual(validate_version(data['pipeline']['projects']['app'],root,{'event':event}),'1.0.0')
                    run.assert_not_called()

    def test_node_and_bun_tests_only_setup_with_registry_port(self):
        for ecosystem in ['npm', 'pnpm', 'bun']:
            with self.subTest(ecosystem=ecosystem), tempfile.TemporaryDirectory() as tmp:
                flags=['setup','--yes','--root',tmp,'--ecosystem',ecosystem,'--test-command','echo ok',
                       '--runtime-image','registry.internal:5000/node','--runner-tag','linux']
                with contextlib.redirect_stdout(io.StringIO()), contextlib.redirect_stderr(io.StringIO()):
                    self.assertEqual(main(flags),0)
                import yaml
                config=yaml.safe_load((Path(tmp)/'ci-platform.yml').read_text())
                self.assertNotIn('container-builder',config); self.assertNotIn('registries',config)
                self.assertEqual(config['allowed-hosts'],['registry.internal'])

    def test_protocol_supports_compatible_patches_but_rejects_unknown_protocol(self):
        _,data=self.compile({'app':service('app')}); data['version']='0.4.9'
        with patch.dict(os.environ,{'TOOLKIT_CONFIG_B64':base64.b64encode(json.dumps(data).encode()).decode()}):
            self.assertEqual(load_config(digest(data)),data)
        data['runtime_protocol']=999
        with patch.dict(os.environ,{'TOOLKIT_CONFIG_B64':base64.b64encode(json.dumps(data).encode()).decode()}):
            with self.assertRaisesRegex(ValueError,'does not match'): load_config(digest(data))

    def test_native_cache_and_automation_events(self):
        p={'checks':{'test':{'script':['true']}},'workflows':{e:{'checks':['test']} for e in ['api','trigger','pipeline']},
           'defaults':{'cache':{'key':{'files':['uv.lock'],'prefix':'uv'},'paths':['.cache/uv']},'variables':{'UV_CACHE_DIR':'$CI_PROJECT_DIR/.cache/uv'}}}
        jobs,_=self.compile({'app':p},infra={'images':{'python':'registry.internal/python'},'allowed-hosts':['registry.internal']})
        for event in ['api','trigger','pipeline']:
            self.assertEqual(jobs[event+':app:test']['cache']['paths'],['.cache/uv'])
            self.assertIn(event,jobs[event+':app:test']['rules'][0]['if'])
        for path in ['.ci-out','.', '**/*', '.venv', 'app/node_modules']:
            p['defaults']['cache']['paths']=[path]
            with self.assertRaises(ValueError): self.compile({'app':p})

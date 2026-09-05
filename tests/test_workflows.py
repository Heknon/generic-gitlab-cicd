import copy
import json
import os
from pathlib import Path
import tempfile
import unittest
from unittest.mock import patch
from generic_ci.workflows.models import Pipeline, Platform
from generic_ci.workflows.compiler import compile_pipeline, merge
from generic_ci.workflows.ecosystems import version_key, extract_version
from generic_ci.workflows.helm import overlay_images
from generic_ci.workflows.runtime import make_plan, run_job
from generic_ci.compiler import digest


def infra():
    return Platform.model_validate({'images': {'python': 'registry.internal/python', 'node': 'registry.internal/node', 'bun': 'registry.internal/bun', 'helm': 'registry.internal/helm'},
        'container-builder': {'image': 'registry.internal/buildah'},
        'registries': {'containers': 'registry.internal/apps', 'previews': 'registry.internal/previews'},
        'allowed-hosts': ['registry.internal'], 'targets': {'test': {'namespace': 'test', 'production': False}}})


def project():
    return {'path': '.', 'checks': {'arbitrary': {'script': ['true']}}, 'workflows': {'push': {'checks': ['arbitrary']}}}

class WorkflowTests(unittest.TestCase):
    def compile(self, config):
        return compile_pipeline(Pipeline.model_validate(config), infra())

    def test_no_hidden_checks(self):
        jobs, data = self.compile({'projects': {'app': project()}})
        self.assertEqual(list(data['nodes']), ['push:app:arbitrary'])
        self.assertNotIn('rules', data['pipeline']['projects']['app']['checks']['arbitrary'])

    def test_merge_preserves_maps_replaces_lists(self):
        result = merge({'tags': ['base'], 'variables': {'A': '1', 'B': '2'}}, {'tags': ['custom'], 'variables': {'A': '3'}})
        self.assertEqual(result, {'tags': ['custom'], 'variables': {'A': '3', 'B': '2'}})

    def test_omitted_overrides_inherit_platform_and_python_policy(self):
        platform = infra()
        platform.defaults.tags = ['internal']
        platform.defaults.variables = {'BASE': 'yes'}
        p = project()
        p['python'] = {'groups': 'all', 'extras': ['sdk']}
        p['checks']['arbitrary']['dependencies'] = {'upgrade': 'all'}
        jobs, data = compile_pipeline(Pipeline.model_validate({'projects': {'app': p}}), platform)
        self.assertEqual(jobs['push:app:arbitrary']['tags'], ['internal'])
        self.assertEqual(jobs['push:app:arbitrary']['variables'], {'BASE': 'yes'})
        self.assertEqual(data['nodes']['push:app:arbitrary']['settings']['dependencies'], {'upgrade': 'all'})

    def test_matrix_gate(self):
        p = project(); p['checks']['arbitrary']['parallel'] = {'matrix': [{'PYTHON_VERSION': ['3.11', '3.14']}]}
        p['container'] = {}; p['workflows']['push']['build'] = True
        jobs, data = self.compile({'projects': {'app': p}})
        self.assertEqual(len(data['nodes']['push:app:build-image']['needs']), 2)
        self.assertNotEqual(jobs['push:app:arbitrary:1']['artifacts']['paths'], jobs['push:app:arbitrary:2']['artifacts']['paths'])

    def test_unknown_check(self):
        p=project(); p['workflows']['push']['checks']=['missing']
        with self.assertRaisesRegex(ValueError, 'unknown check'):
            self.compile({'projects': {'app': p}})

    def test_dependency_cycle(self):
        a=project(); b=project(); a['depends-on']=['b']; b['depends-on']=['a']
        with self.assertRaisesRegex(ValueError, 'project dependency cycle'):
            self.compile({'projects': {'a': a, 'b': b}})

    def test_cross_project_artifacts(self):
        a=project(); a['build']={'script':['true'], 'outputs':['generated']}; a['workflows']['push']['build']=['application']
        b=project(); b['checks']['arbitrary']['needs']=['a.build']
        _,data=self.compile({'projects': {'a':a,'b':b}})
        self.assertEqual(data['nodes']['push:b:arbitrary']['needs'], ['push:a:build'])
        a['workflows']['push']['build']=False
        with self.assertRaisesRegex(ValueError, 'producer'):
            self.compile({'projects': {'a':a,'b':b}})

    def test_release_button_and_bump(self):
        p=project();p['node']={'package-manager':'pnpm'};p['container']={}
        p['release']={'tag':'app/v{version}','create':{}}
        p['workflows']['release']={'checks':['arbitrary'],'build':True}
        jobs,data=self.compile({'projects':{'app':p}})
        self.assertIn('merge-request:app:version', jobs)
        self.assertEqual(jobs['push:app:create-release']['rules'][0]['when'], 'manual')
        self.assertIn('push:app:arbitrary', data['nodes']['push:app:create-release']['needs'])
        self.assertEqual(data['pipeline']['projects']['app']['release']['version']['file'],'package.json')

    def test_semver(self):
        values=['1.0.0-alpha','1.0.0-alpha.1','1.0.0-beta','1.0.0-rc.1','1.0.0','1.0.1']
        self.assertEqual(sorted(values,key=lambda v:version_key(v,True)),values)
        self.assertEqual(version_key('1.0.0+build1',True),version_key('1.0.0+build2',True))
        for v in ['01.0.0','1.0.0-01','1.0.0-a..b']:
            with self.assertRaises(ValueError):version_key(v,True)

    def test_chart_source_conflict(self):
        from generic_ci.workflows.models import Chart
        with self.assertRaises(ValueError):Chart(path='chart',oci='oci://internal/chart',version='1.0.0')

    def test_partial_image_preservation(self):
        bindings=[{'from_':n+'.build-image','set':{'repository':f'{n}.repository','tag':f'{n}.tag'}} for n in ['api','worker']]
        previous={'api':{'repository':'r/api','tag':'1'},'worker':{'repository':'r/worker','tag':'7'}}
        actual=overlay_images(bindings,{'api':{'repository':'r/api','tag':'2'}},previous,True)
        self.assertEqual(actual['worker'],previous['worker']);self.assertEqual(actual['api']['tag'],'2')
        with self.assertRaises(ValueError):overlay_images(bindings,{'api':{'repository':'r/api','tag':'2'}},{},False)

    def test_actual_artifact_handoff_and_failed_receipt(self):
        a=project(); a['build']={'script':['mkdir -p generated; echo hello > generated/input'], 'outputs':['generated']}; a['workflows']['push']['build']=['application']
        b=project(); b['checks']['arbitrary']={'script':['test "$(cat generated/input)" = hello'],'needs':['a.build']}
        _,data=self.compile({'projects':{'a':a,'b':b}});expected=digest(data)
        with tempfile.TemporaryDirectory() as tmp,patch.dict(os.environ,{'CI_PIPELINE_SOURCE':'push','CI_COMMIT_SHA':'a'*40,'CI_PIPELINE_ID':'1'},clear=True):
            root=Path(tmp);make_plan(data,root,expected)
            run_job(data,'push:a:arbitrary',root,expected);run_job(data,'push:a:build',root,expected)
            (root/'generated/input').unlink()
            run_job(data,'push:b:arbitrary',root,expected)
            self.assertTrue((root/'.ci-out/push:b:arbitrary/receipt.json').is_file())
            (root/'.ci-out/push:a:build/files/generated/input').write_text('tampered')
            with self.assertRaisesRegex(ValueError,'modified'):
                run_job(data,'push:b:arbitrary',root,expected)
            self.assertFalse((root/'.ci-out/push:b:arbitrary/receipt.json').exists())

    def test_manual_approval_precedes_fresh_checks(self):
        p=project();p['python']={};p['release']={'tag':'v{version}'};p['container']={};p['workflows']['release']={'checks':['arbitrary'],'build':True}
        deployment={'target':'test','chart':{'path':'chart'},'images':[{'from':'app.build-image','set':{'tag':'image.tag'}}],
                    'before':{'checks':['app.arbitrary']},'workflows':{'release':{'when':'manual'}}}
        jobs,data=self.compile({'projects':{'app':p},'deployments':{'app':deployment}})
        check=data['nodes']['release:app-before:app:arbitrary']
        self.assertIn('release:approve:app',check['needs'])
        self.assertNotIn('when',jobs['release:deploy:app']['rules'][0])

    def test_node_frozen_install_without_network(self):
        import subprocess
        from generic_ci.workflows.ecosystems import environment
        p=project();p['node']={'package-manager':'npm'}
        parsed=Pipeline.model_validate({'projects':{'app':p}}).model_dump()['projects']['app']
        with tempfile.TemporaryDirectory() as tmp, patch.dict(os.environ, {'NPM_CONFIG_REGISTRY':'https://registry.internal','npm_config_offline':'true','npm_config_audit':'false','npm_config_fund':'false'}):
            root=Path(tmp)
            (root/'package.json').write_text(json.dumps({'name':'fixture','version':'1.0.0','private':True}))
            subprocess.run(['npm','install','--package-lock-only','--ignore-scripts','--no-audit','--no-fund'],cwd=root,check=True,capture_output=True)
            before=(root/'package-lock.json').read_bytes()
            with environment(parsed,{},root,'app',[],['registry.internal'],root/'out') as record:
                self.assertEqual(record['manager'],'npm')
            self.assertEqual(before,(root/'package-lock.json').read_bytes())

    def test_buildah_commands_use_pushed_digest(self):
        from generic_ci.workflows.runtime import build_image
        p=project();p['container']={}
        parsed=Pipeline.model_validate({'projects':{'app':p}}).model_dump()['projects']['app']
        data={'pipeline':{'projects':{'app':parsed}},'platform':infra().model_dump()}
        with tempfile.TemporaryDirectory() as tmp,patch.dict(os.environ,{'CI_PIPELINE_ID':'123','CI_COMMIT_SHA':'a'*40}):
            root=Path(tmp);(root/'Dockerfile').write_text('FROM scratch');out=root/'out';out.mkdir()
            calls=[]
            def run(args,**kwargs):
                calls.append(args)
                if args[:2]==['buildah','build']:(out/'iid').write_text('fake-id')
                if args[:2]==['buildah','push']:(out/'digest').write_text('sha256:'+'b'*64)
            with patch('generic_ci.workflows.runtime.subprocess.run',side_effect=run):
                result=build_image(data,{'project':'app','settings':parsed['container']},root,out,{'event':'push','candidates':[]})
            self.assertEqual(result['image']['digest'],'sha256:'+'b'*64)
            self.assertEqual(result['image']['repository'],'registry.internal/previews/app')
            self.assertEqual(calls[-1],['buildah','rmi','fake-id'])

    def test_transitive_change_selection(self):
        a=project();a['path']='a';b=project();b['path']='b';b['depends-on']=['a']
        c=project();c['path']='c';c['depends-on']=['b']
        _,data=self.compile({'projects':{'a':a,'b':b,'c':c}})
        with tempfile.TemporaryDirectory() as tmp,patch.dict(os.environ,{'CI_PIPELINE_SOURCE':'push','CI_COMMIT_BEFORE_SHA':'a'*40,'CI_COMMIT_SHA':'b'*40,'CI_PIPELINE_ID':'1'},clear=True):
            with patch('generic_ci.workflows.runtime.git',return_value='a/code.py'):
                make_plan(data,Path(tmp),digest(data))
            plan=json.loads((Path(tmp)/'.ci-out/plan.json').read_text())
            self.assertEqual(plan['selected'],['a','b','c'])
            self.assertEqual(plan['direct'],['a'])

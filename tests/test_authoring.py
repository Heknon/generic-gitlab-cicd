import contextlib
import io
import json
import os
from pathlib import Path
import tempfile
import unittest
from unittest.mock import patch
import yaml

from generic_ci.cli import main
from generic_ci.authoring import lint


class AuthoringTests(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory(); self.addCleanup(self.temp.cleanup)
        self.root=Path(self.temp.name)
        self.delivery={'projects':{'app':{'path':'app','checks':{'test':{'script':['echo checked']}},'workflows':{'push':{'checks':['test']}}}}}
        self.platform={'images':{'python':'registry.internal/python'},'allowed-hosts':['registry.internal','gitlab.internal']}
        self.save('delivery.yml',self.delivery); self.save('ci-platform.yml',self.platform)
        (self.root/'app').mkdir()

    def save(self,path,value):
        (self.root/path).write_text(yaml.safe_dump(value,sort_keys=False))

    def cli(self,*args):
        output=io.StringIO()
        with contextlib.redirect_stdout(output),contextlib.redirect_stderr(output):
            result=main([*args,'--root',str(self.root)])
        return result,output.getvalue()

    def test_readable_and_json_simulation(self):
        code,text=self.cli('explain','--event','push','--changed','app/main.py')
        self.assertEqual(code,0,text); self.assertIn('RUN push:app:test',text); self.assertIn('$ echo checked',text)
        code,text=self.cli('explain','--event','push','--changed','docs/guide.md','--json')
        self.assertEqual(code,0,text); self.assertEqual(json.loads(text)['selection']['selected'],[])
        self.assertFalse(json.loads(text)['jobs'][0]['selected'])

    def test_doctor_reports_missing_files_without_running_scripts(self):
        self.delivery['projects']['app']['python']={}; self.save('delivery.yml',self.delivery)
        with patch('subprocess.run') as run:
            code,text=self.cli('doctor','--json')
            run.assert_not_called()
        self.assertEqual(code,1,text)
        report=json.loads(text); self.assertFalse(report['valid'])
        self.assertTrue(any('uv.lock' in row['detail'] for row in report['checks']))

    def test_upgrade_preview_apply_and_drift(self):
        before=(self.root/'delivery.yml').read_bytes()
        code,text=self.cli('upgrade','--check'); self.assertEqual(code,1,text)
        self.assertFalse((self.root/'.gitlab-ci.yml').exists())
        code,text=self.cli('upgrade','--apply','--runtime-image','python=registry.internal/python:0.4')
        self.assertEqual(code,0,text); self.assertTrue((self.root/'.generic-ci/delivery.schema.json').is_file())
        self.assertEqual((self.root/'delivery.yml').read_bytes(),before)
        self.assertEqual(self.cli('render','--check','-o',str(self.root/'.gitlab-ci.yml'))[0],0)
        self.assertEqual(self.cli('upgrade','--check')[0],0)
        self.assertEqual(yaml.safe_load((self.root/'ci-platform.yml').read_text())['images']['python'],'registry.internal/python:0.4')

    def test_upgrade_validation_failure_is_nonmutating(self):
        code,text=self.cli('upgrade','--apply','--runtime-image','python=public.example/python')
        self.assertEqual(code,1,text)
        self.assertEqual(yaml.safe_load((self.root/'ci-platform.yml').read_text()),self.platform)
        self.assertFalse((self.root/'.generic-ci').exists())

    def test_upgrade_absolute_input_paths_stay_in_staging(self):
        before=(self.root/'ci-platform.yml').read_bytes()
        code,text=self.cli('upgrade','--config',str(self.root/'delivery.yml'),
                           '--platform',str(self.root/'ci-platform.yml'),
                           '--runtime-image','python=registry.internal/python:new')
        self.assertEqual(code,0,text)
        self.assertEqual((self.root/'ci-platform.yml').read_bytes(),before)
        self.assertFalse((self.root/'.gitlab-ci.yml').exists())

    def test_upgrade_write_failure_restores_previous_files(self):
        before={p.name:p.read_bytes() for p in self.root.iterdir() if p.is_file()}
        original=Path.replace
        count=0
        def fail_second(path,target):
            nonlocal count
            count+=1
            if count==2:raise OSError('simulated disk error')
            return original(path,target)
        with patch.object(Path,'replace',fail_second):
            code,text=self.cli('upgrade','--apply')
        self.assertEqual(code,1,text)
        self.assertEqual({p.name:p.read_bytes() for p in self.root.iterdir() if p.is_file()},before)
        self.assertFalse(list(self.root.rglob('*.generic-ci-tmp')))

    def test_lint_uses_api_without_pipeline_creation_and_redacts_tokens(self):
        response=io.BytesIO(json.dumps({'valid':True,'errors':[],'jobs':[]}).encode())
        with patch.dict(os.environ,{'GENERIC_CI_GITLAB_TOKEN':'private-token'}),patch('urllib.request.OpenerDirector.open',return_value=response) as send:
            report=lint('jobs: {}',{'allowed_hosts': self.platform['allowed-hosts']},'https://gitlab.internal','team/app','main',True)
            request=send.call_args.args[0]
            self.assertEqual(request.full_url,'https://gitlab.internal/api/v4/projects/team%2Fapp/ci/lint')
            self.assertTrue(json.loads(request.data)['dry_run'])
            self.assertTrue(report['valid']); self.assertNotIn('private-token',json.dumps(report))
        self.assertEqual(self.cli('lint','--offline')[0],1)

    def test_invalid_yaml_has_location_without_traceback(self):
        (self.root/'delivery.yml').write_text('projects: [broken')
        code,text=self.cli('validate')
        self.assertEqual(code,1); self.assertIn('line',text); self.assertNotIn('Traceback',text)

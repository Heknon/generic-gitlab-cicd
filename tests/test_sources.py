import contextlib
import io
import json
import os
from pathlib import Path
import subprocess
import tempfile
import unittest
from unittest.mock import patch
import yaml
from generic_ci.cli import main
from generic_ci.sources import load_project, cache_for


class SourceTests(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.addCleanup(self.tmp.cleanup)
        self.root = Path(self.tmp.name)
        self.repo = self.root / 'source'; self.repo.mkdir()
        self.consumer = self.root / 'consumer'; self.consumer.mkdir()
        self.env = patch.dict(os.environ, {'GENERIC_CI_HOME': str(self.root / 'home')})
        self.env.start(); self.addCleanup(self.env.stop)
        self.git('init', '-b', 'main')
        self.put('generic-ci-source.yml', {'version':1,'cli':'>=0.4,<0.5','defaults':{'platform':'defaults/platform.yml'},'templates':{'simple':'templates/simple'}})
        self.put('defaults/platform.yml', {'defaults':{'tags':['internal']},'images':{'python':'registry.internal/python'},'container-builder':{'image':'registry.internal/buildah'},'registries':{'containers':'registry.internal/apps','previews':'registry.internal/previews'},'allowed-hosts':['registry.internal']})
        self.put('templates/simple/delivery.yml', {'projects':{'app':{'path':'.','checks':{'custom':{'script':['true']}},'workflows':{'push':{'checks':['custom']}}}}})
        self.commit()

    def git(self, *args):
        return subprocess.check_output(['git','-C',str(self.repo),*args],text=True,stderr=subprocess.DEVNULL).strip()

    def put(self, path, data):
        p=self.repo/path;p.parent.mkdir(parents=True,exist_ok=True);p.write_text(yaml.safe_dump(data))

    def commit(self):
        self.git('add','.');self.git('-c','user.name=Test','-c','user.email=test@example.com','commit','-m','fixture')
        return self.git('rev-parse','HEAD')

    def cli(self, *args):
        output=io.StringIO()
        with contextlib.redirect_stdout(output),contextlib.redirect_stderr(output):
            result=main(list(args))
        return result,output.getvalue()

    def init(self):
        self.assertEqual(self.cli('source','add','company','--repo',str(self.repo),'--ref','main','--default')[0],0)
        code,text=self.cli('init','--template','simple','--root',str(self.consumer),'--offline')
        self.assertEqual(code,0,text)

    def test_setup_organization_uses_pinned_source_and_schemas(self):
        self.assertEqual(self.cli('source','add','company','--repo',str(self.repo),'--ref','main','--default')[0],0)
        code,text=self.cli('setup','--yes','--mode','organization','--source','company','--template','simple','--root',str(self.consumer),'--offline','--test-command','echo verified')
        self.assertEqual(code,0,text)
        pipeline,platform,_,_=load_project(self.consumer,offline=True)
        self.assertEqual(pipeline.projects['app'].checks['custom'].script,['echo verified'])
        self.assertEqual(platform.defaults.tags,['internal'])
        self.assertTrue((self.consumer/'.generic-ci/platform.schema.json').is_file())
        self.assertEqual(self.cli('render','--root',str(self.consumer),'--offline','--check','-o',str(self.consumer/'.gitlab-ci.yml'))[0],0)

    def test_init_inheritance_render_and_offline(self):
        self.init()
        pipeline,platform,origins,paths=load_project(self.consumer,offline=True)
        self.assertEqual(platform.defaults.tags,['internal'])
        self.assertTrue(origins['platform']['defaults.tags'].startswith('source:'))
        out=self.consumer/'generated.yml'
        self.assertEqual(self.cli('render','--root',str(self.consumer),'--offline','-o',str(out))[0],0)
        self.assertEqual(self.cli('render','--root',str(self.consumer),'--offline','--check','-o',str(out))[0],0)
        config=yaml.safe_load(out.read_text())
        self.assertEqual(config['push:app:custom']['tags'],['internal'])
        self.assertEqual(self.cli('init','--root',str(self.consumer))[0],0)  # lists available templates
        self.assertNotEqual(self.cli('init','--template','simple','--root',str(self.consumer))[0],0)

    def test_pin_update_and_local_override(self):
        self.init();old=json.loads((self.consumer/'generic-ci.lock.json').read_text())
        data=yaml.safe_load((self.repo/'defaults/platform.yml').read_text());data['defaults']['tags']=['new'];self.put('defaults/platform.yml',data);new=self.commit()
        self.assertEqual(load_project(self.consumer)[1].defaults.tags,['internal'])
        code,text=self.cli('source','update','--root',str(self.consumer),'--check')
        self.assertEqual(code,1,text)
        self.assertEqual(json.loads((self.consumer/'generic-ci.lock.json').read_text()),old)
        before=(self.consumer/'delivery.yml').read_bytes()
        self.assertEqual(self.cli('source','update','--root',str(self.consumer))[0],0)
        self.assertEqual(json.loads((self.consumer/'generic-ci.lock.json').read_text())['commit'],new)
        self.assertEqual((self.consumer/'delivery.yml').read_bytes(),before)
        (self.consumer/'local.yml').write_text('defaults:\n  tags: [local]\n')
        _,p,o,_=load_project(self.consumer,platform_override='local.yml')
        self.assertEqual(p.defaults.tags,['local']);self.assertEqual(o['platform']['defaults.tags'],'project:local.yml')

    def test_upgrade_stages_source_change_and_preserves_owned_files(self):
        self.init()
        original=(self.consumer/'delivery.yml').read_bytes()
        before=(self.consumer/'generic-ci.lock.json').read_bytes()
        data=yaml.safe_load((self.repo/'defaults/platform.yml').read_text())
        data['defaults']['tags']=['updated']
        self.put('defaults/platform.yml',data); new=self.commit()
        code,text=self.cli('upgrade','--root',str(self.consumer),'--source-ref','main')
        self.assertEqual(code,0,text)
        self.assertEqual((self.consumer/'generic-ci.lock.json').read_bytes(),before)
        code,text=self.cli('upgrade','--root',str(self.consumer),'--source-ref','main','--runtime-image','python=registry.internal/python:0.4','--apply')
        self.assertEqual(code,0,text)
        self.assertEqual((self.consumer/'delivery.yml').read_bytes(),original)
        self.assertEqual(json.loads((self.consumer/'generic-ci.lock.json').read_text())['commit'],new)
        self.assertEqual(load_project(self.consumer)[1].images['python'],'registry.internal/python:0.4')
        self.assertEqual(self.cli('render','--root',str(self.consumer),'--check','-o',str(self.consumer/'.gitlab-ci.yml'))[0],0)

    def test_bundle_import_on_fresh_cache(self):
        self.init();bundle=self.root/'source.bundle';self.git('bundle','create',str(bundle),'--all')
        with patch.dict(os.environ,{'GENERIC_CI_HOME':str(self.root/'fresh')}):
            self.assertNotEqual(self.cli('validate','--root',str(self.consumer),'--offline')[0],0)
            code,text=self.cli('source','fetch','--root',str(self.consumer),'--from',str(bundle),'--offline')
            self.assertEqual(code,0,text)
            self.assertEqual(self.cli('validate','--root',str(self.consumer),'--offline')[0],0)

    def test_lock_tampering_fails(self):
        self.init();path=self.consumer/'generic-ci.lock.json';lock=json.loads(path.read_text());lock['defaults']['platform']['defaults']['tags']=['tampered'];path.write_text(json.dumps(lock))
        self.assertNotEqual(self.cli('validate','--root',str(self.consumer),'--offline')[0],0)

    def test_symlink_template_is_rejected_before_writes(self):
        (self.repo/'templates/simple/escape').symlink_to('/etc/passwd');self.commit()
        code,text=self.cli('init','--repo',str(self.repo),'--ref','main','--template','simple','--root',str(self.consumer))
        self.assertEqual(code,1,text);self.assertEqual(list(self.consumer.iterdir()),[])

    def test_credentials_and_incompatible_source_rejected(self):
        self.assertNotEqual(self.cli('source','add','bad','--repo','https://token@example.com/a','--ref','main')[0],0)
        data=yaml.safe_load((self.repo/'generic-ci-source.yml').read_text());data['cli']='>=99';self.put('generic-ci-source.yml',data);self.commit()
        self.assertNotEqual(self.cli('source','add','bad','--repo',str(self.repo),'--ref','main')[0],0)


if __name__ == '__main__': unittest.main()

import copy
import importlib.util
import json
import os
from pathlib import Path
import re
import subprocess
import tempfile
import unittest
from unittest.mock import patch
import yaml

ROOT=Path(__file__).resolve().parents[1]

def module(name):
    spec=importlib.util.spec_from_file_location(name,ROOT/'scripts'/f'{name}.py')
    m=importlib.util.module_from_spec(spec); spec.loader.exec_module(m); return m
uv=module('uv_prepare'); version=module('version_check')

def expand(component, values):
    header, body=list(yaml.safe_load_all((ROOT/'templates'/f'{component}.yml').read_text()))
    inputs=header['spec']['inputs']
    if set(values)-set(inputs): raise ValueError('Unknown input')
    supplied={}
    for key,spec in inputs.items():
        if key not in values and 'default' not in spec: raise ValueError('Missing input: '+key)
        value=values.get(key,spec.get('default'))
        if spec['type']=='array' and not isinstance(value,list):raise ValueError('Expected array')
        if spec['type']=='boolean' and not isinstance(value,bool):raise ValueError('Expected bool')
        if spec['type']=='string' and not isinstance(value,str):raise ValueError('Expected string')
        if 'options' in spec and value not in spec['options']:raise ValueError('Invalid option')
        supplied[key]=value
    pattern=re.compile(r'\$\[\[ inputs\.([\w-]+) \]\]')
    def visit(v):
        if isinstance(v,dict):return {visit(k):visit(x) for k,x in v.items()}
        if isinstance(v,list):return [visit(x) for x in v]
        if not isinstance(v,str):return v
        m=pattern.fullmatch(v)
        if m:return copy.deepcopy(supplied[m[1]])
        return pattern.sub(lambda m:str(supplied[m[1]]),v)
    return visit(body)

class Components(unittest.TestCase):
    def test_examples_expand_without_job_collisions(self):
        for p in list((ROOT/'examples').glob('*.yml'))+[ROOT/'images.gitlab-ci.yml',ROOT/'.gitlab-ci.yml']:
            doc=yaml.safe_load(p.read_text()); merged={k:v for k,v in doc.items() if isinstance(v,dict) and 'stage' in v}
            for inc in doc.get('include',[]):
                if 'component' in inc: name=inc['component'].split('/')[-1].split('@')[0]
                elif inc.get('local','').startswith('/templates/'):name=Path(inc['local']).stem
                else:continue
                expanded=expand(name,inc.get('inputs',{}))
                for k,v in expanded.items():
                    self.assertNotIn(k,merged, f'{p}: duplicate {k}')
                    merged[k]=v
            stages=merged.get('stages',doc.get('stages',[]))
            jobs={k:v for k,v in merged.items() if k not in ('stages','workflow')}
            for k,job in jobs.items():
                self.assertIn(job['stage'],stages,f'{p}: {k}')
                for dep in job.get('dependencies',[]):self.assertIn(dep,jobs)
    def test_preview_stop_remains_manual(self):
        jobs=expand('preview',{'name':'api','url':'https://example.com','deploy-commands':['true'],'stop-commands':['true'],'deploy-when':'on_success'})
        self.assertEqual(jobs['api']['when'],'on_success')
        self.assertEqual(jobs['api-stop']['when'],'manual')
        self.assertEqual(jobs['api']['rules'],jobs['api-stop']['rules'])
        self.assertEqual(jobs['api']['resource_group'],jobs['api-stop']['resource_group'])
        self.assertEqual(jobs['api-stop']['variables']['GIT_STRATEGY'],'none')
        self.assertEqual(jobs['api-stop']['dependencies'],[])
    def test_release_artifacts_and_gates(self):
        job=expand('python-publish',{'name':'pub','build-job':'pkg','distribution-path':'ci-dist/pkg'})['pub']
        self.assertEqual(job['dependencies'],['pkg'])
        self.assertNotIn('needs',job)
        self.assertFalse(job['interruptible'])
        self.assertFalse(job['allow_failure'])
        self.assertEqual(job['retry'],0)
    def test_override_guard_executes(self):
        job=expand('deploy',{'name':'dep','commands':['true'],'url':'https://example.com'})['dep']
        for value,expected in [('[]',0),('[{"repo":"x"}]',1)]:
            result=subprocess.run(['sh','-c',job['script'][0]],env={**os.environ,'CI_DEPENDENCY_OVERRIDES':value},capture_output=True)
            self.assertEqual(result.returncode,expected)
    def test_embedded_python_compiles(self):
        for p in (ROOT/'templates').glob('*.yml'):
            text=p.read_text()
            for doc in list(yaml.safe_load_all(text))[1:]:
                for job in doc.values():
                    if not isinstance(job,dict):continue
                    for step in job.get('script',[]):
                        if isinstance(step,str) and step.startswith("python - <<'COMPONENT_PYTHON'"):
                            compile(step.split('\n',1)[1].rsplit('\nCOMPONENT_PYTHON',1)[0],p.name,'exec')
    def test_required_and_typed_inputs(self):
        with self.assertRaises(ValueError):expand('task',{'name':'bad'})
        with self.assertRaises(ValueError):expand('container-build',{'destination':'a:b','push':'true'})

class Overrides(unittest.TestCase):
    def test_sha_and_subdirectory(self):
        req,record=uv.resolve_override({'package':'my-sdk','repo':'https://git.example/team/sdk.git','ref':'a'*40,'subdirectory':'packages/sdk'})
        self.assertIn('@'+'a'*40,req);self.assertTrue(req.endswith('#subdirectory=packages/sdk'))
    def test_reject_credentials_and_bad_paths(self):
        for update in [{'repo':'https://token:secret@git.example/a.git'},{'ref':'main; bad'},{'subdirectory':'../escape'},{'repo':'http://git.example/a.git'},{'package':'bad\nname'}]:
            with self.assertRaises(ValueError):uv.resolve_override({'package':'sdk','repo':'https://git.example/a.git','ref':'main',**update})
    def test_branch_resolved_to_commit(self):
        with patch.object(uv.subprocess,'run',return_value=subprocess.CompletedProcess([],0,'a'*40+'\trefs/heads/feature\n','')):
            req,record=uv.resolve_override({'package':'sdk','repo':'https://git.example/a.git','ref':'feature'})
            self.assertEqual(record['commit'],'a'*40)
    def test_annotated_tag_and_ambiguity(self):
        result='a'*40+'\trefs/tags/v1\n'+'b'*40+'\trefs/tags/v1^{}\n'
        with patch.object(uv.subprocess,'run',return_value=subprocess.CompletedProcess([],0,result,'')):
            self.assertEqual(uv.resolve_override({'package':'sdk','repo':'https://git.example/a.git','ref':'v1'})[1]['commit'],'b'*40)
        result+='c'*40+'\trefs/heads/v1\n'
        with patch.object(uv.subprocess,'run',return_value=subprocess.CompletedProcess([],0,result,'')):
            with self.assertRaises(ValueError):uv.resolve_override({'package':'sdk','repo':'https://git.example/a.git','ref':'v1'})
    def test_restore_manifest_and_lock_when_sync_fails(self):
        with tempfile.TemporaryDirectory() as d:
            old=os.getcwd();os.chdir(d)
            try:
                manifest=b'[project]\nname="app"\nversion="1.0"\ndependencies=["sdk>=2"]\n'
                Path('pyproject.toml').write_bytes(manifest);Path('uv.lock').write_bytes(b'original lock')
                env={'CI_DEPENDENCY_OVERRIDES':json.dumps([{'package':'sdk','repo':'https://git.example/sdk.git','ref':'a'*40}])}
                def fail(*a,**kw):Path('uv.lock').write_text('mutated');raise subprocess.CalledProcessError(1,[])
                with patch.dict(os.environ,env),patch.object(uv.subprocess,'run',side_effect=fail):
                    with self.assertRaises(subprocess.CalledProcessError):uv.main()
                self.assertEqual(Path('pyproject.toml').read_bytes(),manifest)
                self.assertEqual(Path('uv.lock').read_bytes(),b'original lock')
            finally:os.chdir(old)
    def test_normal_path_is_locked(self):
        with tempfile.TemporaryDirectory() as d:
            old=os.getcwd();os.chdir(d)
            try:
                Path('uv.lock').write_text('lock')
                with patch.dict(os.environ,{'CI_DEPENDENCY_OVERRIDES':'[]','CI_DEPENDENCY_REPO':'','CI_DEPENDENCY_REF':'','CI_DEPENDENCY_PACKAGE':''}),patch.object(uv.subprocess,'run') as run:
                    uv.main();self.assertEqual(run.call_args.args[0],['uv','sync','--locked','--all-groups'])
            finally:os.chdir(old)

class Versions(unittest.TestCase):
    def test_pep440_ordering(self):
        self.assertGreater(version.get_version('[project]\nversion="1.10.0"'),version.get_version('[project]\nversion="1.9.0"'))
    def test_dynamic_and_local_rejected(self):
        for raw in ['[project]\ndynamic=["version"]','[project]\nversion="1.0+abc"']:
            with self.assertRaises(ValueError):version.get_version(raw)
    def test_tag_mismatch(self):
        with tempfile.TemporaryDirectory() as d:
            p=Path(d)/'pyproject.toml';p.write_text('[project]\nversion="2.0.0"')
            with patch.dict(os.environ,{'COMPONENT_PYPROJECT':str(p),'CI_COMMIT_TAG':'sdk-v2.0.0','COMPONENT_TAG_PREFIX':'sdk-v'}):version.main()
            with patch.dict(os.environ,{'COMPONENT_PYPROJECT':str(p),'CI_COMMIT_TAG':'v1.0.0','COMPONENT_TAG_PREFIX':'v'}):
                with self.assertRaises(ValueError):version.main()

if __name__=='__main__':unittest.main()

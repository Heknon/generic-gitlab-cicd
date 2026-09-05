import os,tempfile,subprocess,sys,json
from pathlib import Path
sys.path.insert(0,str(Path(__file__).resolve().parents[1]/'scripts'))
from uv_prepare import main
with tempfile.TemporaryDirectory() as d:
 r=Path(d);sdk=r/'sdk';sdk.mkdir();app=r/'app';app.mkdir()
 (sdk/'pyproject.toml').write_text('[project]\nname="demo-sdk"\nversion="1.0.0"\n[build-system]\nrequires=[]\nbuild-backend="backend"\nbackend-path=["."]\n')
 (sdk/'backend.py').write_text('''import zipfile,os
def build_wheel(wheel_directory,config_settings=None,metadata_directory=None):
 name="demo_sdk-1.0.0-py3-none-any.whl"
 with zipfile.ZipFile(os.path.join(wheel_directory,name),"w") as z:
  z.writestr("demo_sdk.py","VALUE = 42\\n")
  z.writestr("demo_sdk-1.0.0.dist-info/METADATA","Metadata-Version: 2.1\\nName: demo-sdk\\nVersion: 1.0.0\\n")
  z.writestr("demo_sdk-1.0.0.dist-info/WHEEL","Wheel-Version: 1.0\\nGenerator: test\\nRoot-Is-Purelib: true\\nTag: py3-none-any\\n")
  z.writestr("demo_sdk-1.0.0.dist-info/RECORD","")
 return name
''')
 def git(*args):return subprocess.check_output(['git','-C',str(sdk),*args],stderr=subprocess.DEVNULL,text=True).strip()
 git('init','-b','feature');git('add','.');git('-c','user.email=test@example.com','-c','user.name=Test','commit','-m','test')
 sha=git('rev-parse','HEAD')
 config=r/'gitconfig';config.write_text(f'[url "file://{sdk}"]\n    insteadOf = https://git.example/test/sdk.git\n')
 os.environ.update({'GIT_CONFIG_GLOBAL':str(config),'UV_PYTHON_DOWNLOADS':'never','CI_PROJECT_DIR':str(app),'COMPONENT_PROVENANCE':'reports/provenance.json','CI_DEPENDENCY_OVERRIDES':json.dumps([{'repo':'https://git.example/test/sdk.git','ref':'feature','package':'demo-sdk'}])})
 os.chdir(app)
 (app/'pyproject.toml').write_text('[project]\nname="demo-app"\nversion="1.0.0"\nrequires-python=">=3.11"\ndependencies=[]\n')
 subprocess.run(['uv','lock'],check=True)
 text=(app/'pyproject.toml').read_text().replace('dependencies=[]','dependencies=["demo-sdk>=2"]')
 (app/'pyproject.toml').write_text(text)
 main()
 assert (app/'pyproject.toml').read_text()==text
 provenance=json.loads((app/'reports/provenance.json').read_text());assert provenance[0]['commit']==sha
 subprocess.run(['.venv/bin/python','-c','import demo_sdk; assert demo_sdk.VALUE == 42'],check=True)
 print('REAL UV OVERRIDE INTEGRATION PASSED: unresolved >=2 requirement replaced by branch package 1.0.0 at verified SHA')

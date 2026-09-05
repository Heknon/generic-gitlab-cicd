"""Run the real target GitLab compiler. Token stays in an environment variable."""
import argparse,json,os,ssl,sys,urllib.parse,urllib.request
from pathlib import Path
p=argparse.ArgumentParser()
p.add_argument('file');p.add_argument('--project',required=True,help='GitLab project numeric ID or namespace/path')
p.add_argument('--ref',required=True,help='Existing branch/tag to simulate')
a=p.parse_args()
base=os.environ['CI_API_V4_URL'].rstrip('/')
if not base.startswith('https://'):raise SystemExit('HTTPS API required')
request=urllib.request.Request(base+'/projects/'+urllib.parse.quote(a.project,safe='')+'/ci/lint',data=json.dumps({'content':Path(a.file).read_text(),'dry_run':True,'ref':a.ref,'include_jobs':True}).encode(),headers={'PRIVATE-TOKEN':os.environ['GITLAB_API_TOKEN'],'Content-Type':'application/json'})
context=ssl.create_default_context(cafile=os.environ.get('SSL_CERT_FILE'))
with urllib.request.urlopen(request,context=context,timeout=60) as response:result=json.load(response)
print(json.dumps({'valid':result.get('valid'),'errors':result.get('errors',[]),'warnings':result.get('warnings',[]),'jobs':[j['name'] for j in result.get('jobs',[])]},indent=2))
sys.exit(0 if result.get('valid') else 1)

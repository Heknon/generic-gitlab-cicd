"""Convert per-image BuildKit metadata into Helm digest overrides."""
import json, os, re
from pathlib import Path

def main():
    rows=json.loads(os.environ.get('COMPONENT_IMAGE_MAP','[]'))
    apps={}
    for row in rows:
        if row['app'] in apps:raise ValueError('Duplicate application image')
        metadata=json.loads(Path(row['metadata']).read_text())
        digest=metadata.get('containerimage.digest','')
        if not re.fullmatch('sha256:[a-f0-9]{64}',digest):raise ValueError('Missing or invalid BuildKit image digest')
        apps[row['app']]={'image':{'repository':os.path.expandvars(row['repository']),'digest':digest}}
    Path('.ci-image-values.json').write_text(json.dumps({'apps':apps})+'\n')
if __name__=='__main__':main()

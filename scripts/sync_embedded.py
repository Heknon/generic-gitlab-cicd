"""Authoring helper: keep self-contained component scripts aligned with tested sources."""
from pathlib import Path
import sys,yaml
root=Path(__file__).resolve().parents[1]
for component,source in [('uv-test','uv_prepare'),('version-check','version_check'),('helm-deploy','image_values'),('helm-preview','image_values')]:
    code=(root/'scripts'/f'{source}.py').read_text()
    if source=='uv_prepare':code=code.replace("os.environ.get('CI_DEPENDENCY_","os.environ.get('COMPONENT_DEPENDENCY_")
    embedded="python - <<'COMPONENT_PYTHON'\n"+code+"\nCOMPONENT_PYTHON"
    p=root/'templates'/f'{component}.yml';docs=list(yaml.safe_load_all(p.read_text()));changed=False
    for job in docs[1].values():
        for n,step in enumerate(job.get('script',[])):
            if isinstance(step,str) and step.startswith("python - <<'COMPONENT_PYTHON'"):
                changed |= step != embedded
                job['script'][n]=embedded
    if '--check' in sys.argv:
        if changed:raise SystemExit(f'{component}: embedded script is stale')
    else:p.write_text(yaml.safe_dump_all(docs,sort_keys=False,width=100))

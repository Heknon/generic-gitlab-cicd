"""Small consumer repositories shared by local execution and real GitLab tests."""
from pathlib import Path
import shutil
import sys

ROOT = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(ROOT))
import yaml
from generic_ci.workflows.compiler import compile_pipeline
from generic_ci.workflows.models import Pipeline, Platform

SCENARIOS = ('handoff', 'failed-gate', 'matrix', 'selection')


def fixture(root, scenario, image='fixture.internal/python', event='push'):
    """Keep generated scripts, rules, needs and artifacts intact in both runners."""
    if scenario not in SCENARIOS:
        raise ValueError('unknown scenario: ' + scenario)
    a = {'path': 'producer', 'checks': {'verify': {'script': ['true']}},
         'build': {'script': ['export VALUE=artifact-proof', 'mkdir -p generated', 'echo "$VALUE" > generated/payload'],
                   'outputs': ['generated']},
         'workflows': {event: {'checks': ['verify'], 'build': ['application']}}}
    b = {'path': 'consumer', 'depends-on': ['producer'], 'checks': {'consume': {
        'script': ['test "$(cat ../producer/generated/payload)" = artifact-proof'], 'needs': ['producer.build']}},
        'workflows': {event: {'checks': ['consume']}}}
    if scenario == 'failed-gate':
        a['checks']['verify']['script'] = ['echo expected-gate-failure; exit 7']
    if scenario == 'matrix':
        a['checks']['verify'].update(parallel={'matrix': [{'FLAVOR': ['one', 'two']}]},
                                    script=['test "$FLAVOR" = one || test "$FLAVOR" = two'])
    if scenario == 'selection':
        b.pop('depends-on')
        b['checks']['consume'] = {'script': ['echo unexpected-consumer; exit 9']}
    config = {'projects': {'producer': a, 'consumer': b}}
    host = image.split('/')[0]
    infra = {'defaults': {'tags': ['generic-ci-fixture']}, 'images': {'python': image}, 'container-builder': {'image': image},
             'registries': {'containers': host + '/prod', 'previews': host + '/preview'},
             'allowed-hosts': [host]}
    jobs, payload = compile_pipeline(Pipeline.model_validate(config), Platform.model_validate(infra))
    for name in ('producer', 'consumer'):
        (root / name).mkdir(parents=True, exist_ok=True)
        (root / name / 'README').write_text(name + '\n')
    shutil.copytree(ROOT / 'generic_ci', root / 'generic_ci', ignore=shutil.ignore_patterns('__pycache__'), dirs_exist_ok=True)
    (root / 'delivery.yml').write_text(yaml.safe_dump(config, sort_keys=False))
    (root / 'ci-platform.yml').write_text(yaml.safe_dump(infra, sort_keys=False))
    (root / '.gitlab-ci.yml').write_text(yaml.safe_dump(jobs, sort_keys=False))
    return jobs, payload

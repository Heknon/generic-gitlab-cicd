"""Keep the portable skill's reference bundle aligned with maintained repo docs."""
import argparse
from pathlib import Path
import shutil

ROOT = Path(__file__).resolve().parents[1]
DEST = ROOT / 'skills/generic-ci-authoring/references'


def main():
    parser = argparse.ArgumentParser()
    parser.add_argument('--check', action='store_true')
    args = parser.parse_args()
    mappings = {
        ROOT / 'docs/ai-authoring-revision-three.md': DEST / 'authoring.md',
        ROOT / 'docs/cli-reference.md': DEST / 'cli.md',
        ROOT / 'docs/workflows-revision-one.md': DEST / 'workflows.md',
        ROOT / 'docs/configuration-sources-revision-two.md': DEST / 'sources.md',
    }
    for path in (ROOT / 'examples/workflows').glob('*.yml'):
        mappings[path] = DEST / 'examples' / path.name
    for name in ['workflows', 'workflows-platform', 'source', 'source-project']:
        mappings[ROOT / 'schemas' / (name + '.schema.json')] = DEST / 'schemas' / (name + '.schema.json')
    stale = []
    for source, target in mappings.items():
        if args.check:
            if not target.is_file() or source.read_bytes() != target.read_bytes():
                stale.append(str(target.relative_to(ROOT)))
        else:
            target.parent.mkdir(parents=True, exist_ok=True)
            shutil.copyfile(source, target)
    if stale:
        parser.exit(1, 'Stale skill references; run scripts/sync_authoring_skill.py:\n' + '\n'.join(stale) + '\n')
    print('Authoring skill reference bundle is current.')


if __name__ == '__main__':
    main()

import contextlib
import io
import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch
import yaml
from generic_ci.cli import main


class SetupTests(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.addCleanup(self.temp.cleanup)
        self.root = Path(self.temp.name)
        self.flags = ['setup', '--yes', '--root', str(self.root), '--test-command', 'echo test',
                      '--runtime-image', 'registry.internal/python', '--builder-image', 'registry.internal/buildah',
                      '--registry', 'registry.internal/apps', '--preview-registry', 'registry.internal/previews', '--runner-tag', 'linux']

    def run_cli(self, args):
        with contextlib.redirect_stdout(io.StringIO()), contextlib.redirect_stderr(io.StringIO()):
            return main(args)

    def test_standalone_schema_and_drift(self):
        self.assertEqual(self.run_cli(self.flags), 0)
        self.assertTrue((self.root/'.generic-ci/delivery.schema.json').is_file())
        self.assertTrue((self.root/'delivery.yml').read_text().startswith('# yaml-language-server:'))
        self.assertEqual(self.run_cli(['render','--root',str(self.root),'--check','-o',str(self.root/'.gitlab-ci.yml')]),0)
        self.assertFalse((self.root/'.pre-commit-config.yaml').exists())

    def test_dry_run_and_conflict_leave_files_untouched(self):
        self.assertEqual(self.run_cli(self.flags+['--dry-run']),0)
        self.assertEqual(list(self.root.iterdir()),[])
        (self.root/'.gitlab-ci.yml').write_text('existing')
        self.assertEqual(self.run_cli(self.flags),1)
        self.assertEqual([p.name for p in self.root.iterdir()],['.gitlab-ci.yml'])
        self.assertEqual((self.root/'.gitlab-ci.yml').read_text(),'existing')

    def test_invalid_and_symlink_fail_before_writing(self):
        self.assertEqual(self.run_cli(self.flags+['--app','INVALID']),1)
        self.assertEqual(list(self.root.iterdir()),[])
        (self.root/'.generic-ci').symlink_to(self.root, target_is_directory=True)
        self.assertEqual(self.run_cli(self.flags),1)
        self.assertFalse((self.root/'delivery.yml').exists())

    def test_preview_chart_and_manual_gate(self):
        (self.root/'Dockerfile').write_text('FROM internal/base')
        args=['--deploy','yes','--helm-image','registry.internal/helm','--chart-oci','oci://registry.internal/charts/generic-app',
              '--chart-version','2.1.0','--namespace','preview','--hostname','auto']
        self.assertEqual(self.run_cli(self.flags+args),0)
        values=yaml.safe_load((self.root/'deploy/values.yaml').read_text())
        self.assertNotIn('host',values['apps']['app']['route'])
        jobs=yaml.safe_load((self.root/'.gitlab-ci.yml').read_text())
        self.assertTrue(any(j.get('rules',[{}])[0].get('when')=='manual' for j in jobs.values() if isinstance(j,dict)))

    def test_interactive_cancel(self):
        flags=[a for a in self.flags if a!='--yes']
        with patch('sys.stdin.isatty',return_value=True), patch('builtins.input',side_effect=['standalone','app','.','generic','no','n']):
            self.assertEqual(self.run_cli(flags),0)
        self.assertEqual(list(self.root.iterdir()),[])

    def test_missing_unattended_input(self):
        self.assertEqual(self.run_cli(['setup','--yes','--root',str(self.root)]),1)
        self.assertEqual(list(self.root.iterdir()),[])

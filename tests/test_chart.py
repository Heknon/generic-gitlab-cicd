"""Render the shared chart when Helm is installed; no cluster or registry calls."""
from pathlib import Path
import shutil
import subprocess
import tempfile
import unittest
import yaml

ROOT = Path(__file__).resolve().parents[1]
HELM = shutil.which('helm')


@unittest.skipUnless(HELM, 'Helm is required for chart render tests')
class ChartTests(unittest.TestCase):
    def render(self, change=None):
        values = yaml.safe_load((ROOT / 'examples/helm-values.yaml').read_text())
        if change:
            change(values['apps']['api'])
        with tempfile.TemporaryDirectory() as tmp:
            file = Path(tmp) / 'values.yaml'
            file.write_text(yaml.safe_dump(values))
            output = subprocess.check_output([HELM, 'template', 'smoke', str(ROOT / 'charts/generic-app'), '-f', str(file)], text=True)
        return [doc for doc in yaml.safe_load_all(output) if doc]

    def test_tags_and_route_service_binding(self):
        resources = self.render()
        self.assertNotIn('Ingress', [r['kind'] for r in resources])
        route = next(r for r in resources if r['kind'] == 'Route')
        service = next(r for r in resources if r['kind'] == 'Service')
        self.assertEqual(route['apiVersion'], 'route.openshift.io/v1')
        self.assertEqual(route['spec']['to']['name'], service['metadata']['name'])
        self.assertEqual(route['spec']['port']['targetPort'], service['spec']['ports'][0]['name'])
        deployments = [r for r in resources if r['kind'] == 'Deployment']
        self.assertEqual(len(deployments), 2)
        for deployment in deployments:
            self.assertTrue(deployment['spec']['template']['spec']['containers'][0]['image'].endswith(':1.5.0'))

    def test_tls_fields_preserve_multiline_pem(self):
        tls = {'termination': 'reencrypt', 'insecureEdgeTerminationPolicy': 'Redirect',
               'certificate': '-----BEGIN CERTIFICATE-----\nfixture\n-----END CERTIFICATE-----\n',
               'key': '-----BEGIN PRIVATE KEY-----\nfixture\n-----END PRIVATE KEY-----\n',
               'caCertificate': 'chain\nsecond-line\n', 'destinationCACertificate': 'backend-ca\n'}
        resources = self.render(lambda app: app['route'].update(tls=tls))
        route = next(r for r in resources if r['kind'] == 'Route')
        self.assertEqual(route['spec']['tls'], tls)

    def test_plain_passthrough_and_disabled_routes(self):
        resources = self.render(lambda app: app['route'].pop('tls'))
        self.assertNotIn('tls', next(r for r in resources if r['kind'] == 'Route')['spec'])
        resources = self.render(lambda app: app['route'].update(tls={'termination': 'passthrough'}))
        self.assertEqual(next(r for r in resources if r['kind'] == 'Route')['spec']['tls'], {'termination': 'passthrough'})
        resources = self.render(lambda app: app.pop('route'))
        self.assertNotIn('Route', [r['kind'] for r in resources])

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

    def api_pod(self, resources):
        return next(r for r in resources if r['kind'] == 'Deployment' and r['metadata']['labels']['app.kubernetes.io/name'] == 'api')['spec']['template']

    def test_relaxed_defaults_and_optional_probes_resources(self):
        def change(app):
            app.pop('resources')
            app.pop('readinessProbe')
            app.pop('livenessProbe', None)
        pod = self.api_pod(self.render(change))['spec']
        container = pod['containers'][0]
        self.assertNotIn('securityContext', pod)
        self.assertNotIn('automountServiceAccountToken', pod)
        self.assertNotIn('securityContext', container)
        self.assertNotIn('resources', container)
        self.assertNotIn('readinessProbe', container)
        self.assertNotIn('livenessProbe', container)

    def test_explicit_security_and_volumes_pass_through(self):
        volumes = [{'name': 'settings', 'configMap': {'name': 'api-settings'}},
                   {'name': 'data', 'persistentVolumeClaim': {'claimName': 'api-data'}},
                   {'name': 'tmp', 'emptyDir': {}}]
        mounts = [{'name': 'settings', 'mountPath': '/app/config', 'readOnly': True},
                  {'name': 'data', 'mountPath': '/data'}, {'name': 'tmp', 'mountPath': '/tmp'}]
        def change(app):
            app.update(volumes=volumes, volumeMounts=mounts, automountServiceAccountToken=False,
                       podSecurityContext={'runAsNonRoot': True, 'fsGroup': 2000},
                       securityContext={'readOnlyRootFilesystem': True, 'allowPrivilegeEscalation': False},
                       resources={'requests': {'memory': '128Mi'}})
        pod = self.api_pod(self.render(change))['spec']
        container = pod['containers'][0]
        self.assertEqual(pod['volumes'], volumes)
        self.assertEqual(container['volumeMounts'], mounts)
        self.assertFalse(pod['automountServiceAccountToken'])
        self.assertEqual(pod['securityContext']['fsGroup'], 2000)
        self.assertTrue(container['securityContext']['readOnlyRootFilesystem'])
        self.assertNotIn('limits', container['resources'])

    def test_named_ports_and_monitoring_metadata(self):
        ports = [{'name': 'web', 'containerPort': 8080}, {'name': 'metrics', 'containerPort': 9090}]
        service_ports = [{'name': 'web', 'port': 80, 'targetPort': 'web'},
                         {'name': 'metrics', 'port': 9090, 'targetPort': 'metrics'}]
        def change(app):
            app.update(ports=ports, podLabels={'team': 'backend'}, podAnnotations={'prometheus.io/scrape': 'true'})
            app['service'] = {'enabled': True, 'ports': service_ports, 'labels': {'monitor': 'api'}}
            app['route']['targetPort'] = 'web'
        resources = self.render(change)
        pod = self.api_pod(resources)
        self.assertEqual(pod['spec']['containers'][0]['ports'], ports)
        self.assertEqual(pod['metadata']['labels']['team'], 'backend')
        self.assertEqual(pod['metadata']['labels']['app.kubernetes.io/name'], 'api')
        self.assertEqual(pod['metadata']['annotations']['prometheus.io/scrape'], 'true')
        self.assertIn('checksum/config', pod['metadata']['annotations'])
        service = next(r for r in resources if r['kind'] == 'Service')
        self.assertEqual(service['spec']['ports'], service_ports)
        self.assertEqual(service['metadata']['labels'], {'monitor': 'api'})
        self.assertEqual(next(r for r in resources if r['kind'] == 'Route')['spec']['port']['targetPort'], 'web')

    def test_ports_without_service_and_explicit_empty_ports(self):
        def change(app):
            app['service'] = {'enabled': False}
            app['ports'] = [{'name': 'metrics', 'containerPort': 9090}]
        resources = self.render(change)
        self.assertNotIn('Service', [r['kind'] for r in resources])
        self.assertEqual(self.api_pod(resources)['spec']['containers'][0]['ports'][0]['containerPort'], 9090)
        resources = self.render(lambda app: app.update(ports=[]))
        self.assertNotIn('ports', self.api_pod(resources)['spec']['containers'][0])

import base64
import copy
import json
import os
from pathlib import Path
import subprocess
import tempfile
import unittest
from unittest.mock import patch

import yaml

from generic_ci.compiler import compile_pipeline, digest, render, variants
from generic_ci.config import affected, load, read_yaml
from generic_ci.dependencies import prepared, resolve_candidates, workspace
from generic_ci.models import Dependencies
from generic_ci.runtime import make_plan, materialize, require_receipt, run_job, write_json


PLATFORM = {
    "runtimes": {"default": {"tags": ["linux"]}, "windows": {"tags": ["windows"], "shell": "powershell"}},
    "registry": "registry.internal/releases", "preview-registry": "registry.internal/previews",
    "chart": "charts/app", "chart-version": "1.0.0",
    "allowed-hosts": ["registry.internal", "git.internal", "packages.internal"],
    "targets": {"staging": {"namespace": "staging", "production": False},
                "production": {"namespace": "production"}},
}


class Product(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.addCleanup(self.temp.cleanup)
        self.root = Path(self.temp.name)

    def compile(self, projects, platform=None):
        (self.root / "delivery.yml").write_text(yaml.safe_dump({"projects": projects}))
        (self.root / "platform.yml").write_text(yaml.safe_dump(platform or PLATFORM))
        p, infra, origins = load(self.root / "delivery.yml", self.root / "platform.yml")
        jobs, data = compile_pipeline(p, infra)
        return jobs, data

    def test_small_package_defaults_and_disable(self):
        jobs, data = self.compile({"sdk": {"preset": "python-package", "checks": {"lint": False}}})
        self.assertNotIn("sdk-lint", jobs)
        self.assertEqual(data["nodes"]["sdk-package"]["needs"], ["sdk-unit"])
        self.assertNotIn("trigger", jobs["toolkit-plan"])

    def test_matrix_all_required_and_output_isolation(self):
        jobs, data = self.compile({"api": {"checks": {"unit": {"script": ["true"], "gitlab": {
            "parallel": {"matrix": [{"PYTHON_VERSION": ["3.11", "3.13", "3.14"]}]}}}}, "container": {}}})
        self.assertEqual(data["nodes"]["api-container"]["needs"], ["api-unit-1", "api-unit-2", "api-unit-3"])
        self.assertEqual(len({jobs[k]["artifacts"]["paths"][0] for k in data["nodes"]}), 4)

    def test_custom_rules_are_preserved_and_missing_gate_fails(self):
        rules = [{"if": '$CI_PIPELINE_SOURCE == "merge_request_event"'}]
        jobs, data = self.compile({"api": {"checks": {"unit": {"script": ["true"], "gitlab": {"rules": rules}}}, "container": {}}})
        self.assertEqual(jobs["api-unit"]["rules"], rules)
        with self.assertRaisesRegex(ValueError, "no success receipt"):
            require_receipt(self.root, "api-unit", "hash")

    def test_cycles_and_missing_references_rejected(self):
        for needs in (["missing"], ["b"]):
            with self.assertRaises(ValueError):
                self.compile({"api": {"steps": {"a": {"script": ["true"], "needs": needs},
                                                       "b": {"script": ["true"], "needs": ["a"]}}}})

    def test_required_failure_tolerance_rejected(self):
        with self.assertRaisesRegex(ValueError, "cannot allow failure"):
            self.compile({"api": {"checks": {"unit": {"script": ["false"], "gitlab": {"allow_failure": True}}}, "container": {}}})

    def test_disabled_required_and_mandatory_rejected(self):
        infra = copy.deepcopy(PLATFORM)
        infra["mandatory-checks"] = ["unit"]
        with self.assertRaisesRegex(ValueError, "missing/disabled"):
            self.compile({"api": {"preset": "python-service", "checks": {"unit": False}}}, infra)

    def test_unknown_field_duplicate_key_and_non_string_key(self):
        with self.assertRaises(ValueError):
            self.compile({"api": {"cheks": {}}})
        for text in ("projects: {}\nprojects: {}\n", "on: []\n"):
            p = self.root / "duplicate.yml"
            p.write_text(text)
            with self.assertRaises(ValueError):
                read_yaml(p)

    def test_forbid_public_endpoints_and_native_owned_fields(self):
        infra = copy.deepcopy(PLATFORM)
        infra["registry"] = "docker.io/team"
        with self.assertRaisesRegex(ValueError, "allowed"):
            self.compile({"api": {}}, infra)
        with self.assertRaises(ValueError):
            self.compile({"api": {"checks": {"unit": {"script": ["true"], "gitlab": {"needs": []}}}}})

    def test_preview_stop_uses_no_checkout_or_artifacts(self):
        jobs, data = self.compile({"api": {"container": {}, "deploy": {"review": {"target": "staging", "preview": True}}}})
        start, stop = jobs["api-deploy-review"], jobs["api-stop-review"]
        self.assertEqual(start["rules"], stop["rules"])
        self.assertEqual(start["resource_group"], stop["resource_group"])
        self.assertEqual(stop["needs"], [])
        self.assertEqual(stop["variables"]["GIT_STRATEGY"], "none")
        self.assertEqual(stop["when"], "manual")

    def test_custom_chart_requires_binding(self):
        with self.assertRaisesRegex(ValueError, "image bindings"):
            self.compile({"api": {"container": {}, "deploy": {"stage": {"target": "staging", "chart": "custom"}}}})

    def test_matrix_limits_and_reserved_variables(self):
        with self.assertRaisesRegex(ValueError, "200"):
            variants({"parallel": {"matrix": [{"VERSION": list(range(201))}]}})
        with self.assertRaisesRegex(ValueError, "duplicate"):
            variants({"parallel": {"matrix": [{"VERSION": ["1", "1"]}]}})
        with self.assertRaises(ValueError):
            self.compile({"api": {"checks": {"unit": {"script": ["true"], "gitlab": {"variables": {"CI_JOB_TOKEN": "x"}}}}}})

    def test_effective_graph_selects_shared_project(self):
        _, data = self.compile({"common": {}, "api": {"depends-on": ["common"]}, "other": {"path": "other"}})
        data["pipeline"]["projects"]["common"]["path"] = "common"
        data["pipeline"]["projects"]["api"]["path"] = "api"
        self.assertEqual(affected(data["pipeline"], ["common/deleted.py"]), {"common", "api"})
        self.assertEqual(affected(data["pipeline"], None), {"common", "api", "other"})

    def test_runtime_receipts_step_handoff_and_stale_rejection(self):
        _, data = self.compile({"api": {"steps": {"generate": {
            "script": ["printf 'hello' > output.txt"], "outputs": {"message": "output.txt"}}},
            "checks": {"consume": {"needs": ["generate"], "script": ["test -f output.txt"]}}}})
        expected = digest(data)
        env = {"CI_PROJECT_DIR": str(self.root), "CI_COMMIT_SHA": "a" * 40, "CI_PIPELINE_ID": "1", "CI_FULL_PIPELINE": "true",
               "CI_DEPENDENCY_OVERRIDES": "[]", "CI_DEPENDENCY_FILE": "", "CI_DEPENDENCY_REPO": "", "CI_DEPENDENCY_REF": "", "CI_DEPENDENCY_PACKAGE": ""}
        with patch.dict(os.environ, env):
            make_plan(data, self.root, expected)
            run_job(data, "api-generate", self.root, expected)
            (self.root / "output.txt").unlink()
            run_job(data, "api-consume", self.root, expected)
            self.assertEqual((self.root / "output.txt").read_text(), "hello")
            with patch.dict(os.environ, {"CI_PIPELINE_ID": "2"}):
                with self.assertRaisesRegex(ValueError, "stale"):
                    require_receipt(self.root, "api-consume", expected)

    def test_failed_command_never_writes_success(self):
        _, data = self.compile({"api": {"checks": {"unit": {"script": ["exit 3"]}}}})
        with patch.dict(os.environ, {"CI_FULL_PIPELINE": "true", "CI_DEPENDENCY_OVERRIDES": "[]", "CI_DEPENDENCY_FILE": "",
                                     "CI_DEPENDENCY_REPO": "", "CI_DEPENDENCY_REF": "", "CI_DEPENDENCY_PACKAGE": ""}):
            make_plan(data, self.root, digest(data))
            with self.assertRaises(subprocess.CalledProcessError):
                run_job(data, "api-unit", self.root, digest(data))
            self.assertFalse((self.root / ".ci-out/api-unit/receipt.json").exists())

    def test_override_input_exclusivity_and_scope(self):
        candidate = {"repository": "https://git.internal/sdk.git", "ref": "a" * 40, "package": "sdk", "projects": ["api"]}
        resolved = resolve_candidates({"CI_DEPENDENCY_OVERRIDES": json.dumps([candidate])}, PLATFORM["allowed-hosts"], ["api"])
        self.assertEqual(resolved[0]["commit"], "a" * 40)
        with self.assertRaisesRegex(ValueError, "only one"):
            resolve_candidates({"CI_DEPENDENCY_OVERRIDES": json.dumps([candidate]), "CI_DEPENDENCY_FILE": "anything"}, PLATFORM["allowed-hosts"], ["api"])

    def test_workspace_member_sync_real_uv(self):
        (self.root / "packages/member").mkdir(parents=True)
        (self.root / "pyproject.toml").write_text('[project]\nname="root"\nversion="1.0"\nrequires-python=">=3.11"\n[tool.uv.workspace]\nmembers=["packages/*"]\n')
        member = self.root / "packages/member"
        (member / "pyproject.toml").write_text('[project]\nname="member"\nversion="1.0"\nrequires-python=">=3.11"\n')
        subprocess.run(["uv", "lock", "--offline"], cwd=self.root, check=True, capture_output=True)
        original = (self.root / "uv.lock").read_bytes()
        env = {k: "" for k in ("PIP_INDEX_URL", "PIP_EXTRA_INDEX_URL", "UV_INDEX_URL", "UV_DEFAULT_INDEX", "UV_EXTRA_INDEX_URL")}
        with patch.dict(os.environ, env):
            with prepared(member, self.root, Dependencies(groups=[]).model_dump(), [], "api", PLATFORM["allowed-hosts"], self.root / "evidence") as result:
                self.assertEqual(result["workspace"], ".")
                self.assertTrue(Path(result["interpreter"]).is_file())
        self.assertEqual((self.root / "uv.lock").read_bytes(), original)


if __name__ == "__main__":
    unittest.main()

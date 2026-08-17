import json
import re
import subprocess
import unittest
from pathlib import Path


REPOSITORY_ROOT = Path(__file__).resolve().parents[1].parent
AMPLIFY_FILE = REPOSITORY_ROOT / "amplify.yml"


class AmplifyConfigurationTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.content = AMPLIFY_FILE.read_text(encoding="utf-8")
        parsed = subprocess.run(
            [
                "ruby",
                "-e",
                (
                    'require "yaml"; require "json"; '
                    'print JSON.generate(YAML.load_file(ARGV[0]))'
                ),
                str(AMPLIFY_FILE),
            ],
            capture_output=True,
            text=True,
            check=True,
        )
        cls.parsed = json.loads(parsed.stdout)

    def test_app_root_is_frontend(self):
        self.assertEqual(
            self.parsed["applications"][0]["appRoot"],
            "frontend",
        )

    def test_npm_ci_is_used(self):
        prebuild_commands = self.parsed["applications"][0]["frontend"]["phases"]["preBuild"]["commands"]
        self.assertIn("npm ci", prebuild_commands)

    def test_frontend_tests_run(self):
        build_commands = self.parsed["applications"][0]["frontend"]["phases"]["build"]["commands"]
        self.assertIn("npm run test", build_commands)

    def test_build_staging_is_used(self):
        build_commands = self.parsed["applications"][0]["frontend"]["phases"]["build"]["commands"]
        self.assertIn("npm run build:staging", build_commands)

    def test_dist_is_artifact_directory(self):
        self.assertEqual(
            self.parsed["applications"][0]["frontend"]["artifacts"]["baseDirectory"],
            "frontend/dist",
        )

    def test_artifact_files_are_globbed(self):
        self.assertEqual(
            self.parsed["applications"][0]["frontend"]["artifacts"]["files"],
            ["**/*"],
        )

    def test_no_development_or_production_build_command_is_used(self):
        self.assertNotIn("npm run build:dev", self.content)
        self.assertNotIn("npm run build:prod", self.content)
        self.assertNotIn("vite build --mode development", self.content)
        self.assertNotIn("vite build --mode production", self.content)

    def test_no_secret_or_actual_jm8_values_are_embedded(self):
        forbidden_patterns = [
            r"sk_test_[A-Za-z0-9_]+",
            r"sk_live_[A-Za-z0-9_]+",
            r"whsec_[A-Za-z0-9_]+",
            r"AKIA[0-9A-Z]{16}",
            r"a1b2c3d4e5",
            r"client-fake-123",
        ]

        for pattern in forbidden_patterns:
            with self.subTest(pattern=pattern):
                self.assertIsNone(re.search(pattern, self.content))

    def test_no_backend_deployment_command_is_used(self):
        forbidden = [
            "aws ",
            "provision-stripe-secret",
            "create-api",
            "create-resources",
            "backend/bin/deploy",
            "backend/bin/provision-stripe-secret",
            "backend/bin/create-api",
            "backend/bin/create-resources",
        ]

        for marker in forbidden:
            with self.subTest(marker=marker):
                self.assertNotIn(marker, self.content)

    def test_spa_rewrite_is_documented(self):
        self.assertIn("source: /<*>", self.content)
        self.assertIn("target: /index.html", self.content)
        self.assertIn("status: 200", self.content)
        self.assertIn("rewrite", self.content)

    def test_basic_auth_note_is_documented(self):
        self.assertIn("basic authentication", self.content)
        self.assertIn("public beta", self.content)
        self.assertIn("Amplify branch settings", self.content)


if __name__ == "__main__":
    unittest.main(verbosity=2)

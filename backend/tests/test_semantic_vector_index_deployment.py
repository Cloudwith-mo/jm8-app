from __future__ import annotations

import json
from pathlib import Path
import stat
import unittest
from unittest.mock import patch


BACKEND_ROOT = Path(__file__).resolve().parents[1]
BIN_DIR = BACKEND_ROOT / "bin"


class SemanticVectorIndexDeploymentTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.path = BIN_DIR / "deploy-semantic-vector-index"
        cls.script = cls.path.read_text(encoding="utf-8")

    def test_script_is_executable_and_uses_shared_stage_guard(self):
        self.assertTrue(self.path.stat().st_mode & stat.S_IXUSR)
        self.assertIn('source "$SCRIPT_DIR/jm8_deployment_guard.sh"', self.script)
        self.assertIn(
            'export JM8_OPERATION="deploy-semantic-vector-index"',
            self.script,
        )
        self.assertIn("jm8_validate_contract_or_exit", self.script)

    def test_update_payload_is_exact_and_tenant_partitioned(self):
        body = self.script.split(
            '> "$BUILD_DIR/vector-index-update.json" <<\'PY\'\n',
            1,
        )[1].split("\nPY\n", 1)[0]
        namespace = {}
        output = []
        original_dumps = json.dumps

        def capture(value, **kwargs):
            output.append(value)
            return original_dumps(value, **kwargs)

        with patch("json.dumps", side_effect=capture):
            exec(compile(body, "vector-index-update", "exec"), namespace)
        self.assertEqual(output[0], [{"Create": {
            "IndexName": "SemanticEmbeddingIndex",
            "VectorAttribute": {"AttributeName": "embedding"},
            "SearchSchema": [{
                "AttributeName": "embeddingPartition",
                "SearchSchemaElementType": "HASH",
            }],
            "Projection": {"ProjectionType": "KEYS_ONLY"},
            "Dimensions": 1024,
            "DistanceFunction": "COSINE",
        }}])
        self.assertIn(
            '"AttributeName=${PARTITION_ATTRIBUTE},AttributeType=S"',
            self.script,
        )

    def test_reconciliation_is_idempotent_and_never_deletes_an_index(self):
        self.assertIn('CREATE)', self.script)
        self.assertIn('WAIT)', self.script)
        self.assertIn('READY)', self.script)
        self.assertIn('MAX_ATTEMPTS=180', self.script)
        self.assertIn('POLL_SECONDS=10', self.script)
        self.assertNotIn('"Delete"', self.script)
        self.assertNotIn("delete-table", self.script)

    def test_main_deploy_orders_index_before_embedding_worker(self):
        deploy = (BIN_DIR / "deploy").read_text(encoding="utf-8")
        self.assertLess(
            deploy.index("./bin/deploy-semantic-vector-index"),
            deploy.index("./bin/deploy-semantic-embedding"),
        )


if __name__ == "__main__":
    unittest.main()

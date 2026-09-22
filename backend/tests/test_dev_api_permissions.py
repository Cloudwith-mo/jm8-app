"""Local boundary checks; live IAM simulation is still a release gate."""
import fnmatch
import sys
import unittest
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "bin"))
from jm8_github_oidc_bootstrap import stage_policies

API = "arn:aws:apigateway:us-east-1::/apis/u06tdrfsua"
TAG = "arn:aws:apigateway:us-east-1::/tags/" + API


def allows(action, resource, tags=None):
    # Evaluate only the added grants, not a substitute for AWS's IAM evaluator.
    for s in stage_policies("dev", "us-east-1")["compute"]["Statement"]:
        if s["Sid"] not in {"WriteExactDevHttpApiTags",
                            "ManageExactDevHttpApiCollections",
                            "ManageExactDevHttpApiChildren"}:
            continue
        resources = s["Resource"]
        if isinstance(resources, str):
            resources = [resources]
        if action not in s["Action"] or not any(fnmatch.fnmatchcase(resource, r) for r in resources):
            continue
        if "Condition" in s:
            tags = tags or {}
            expected = {k.split("/", 1)[1]: v for k, v in
                        s["Condition"]["StringEquals"].items()}
            if tags != expected:
                continue
        return True
    return False


class DevApiPermissions(unittest.TestCase):
    def test_required_child_calls_without_inherited_tags(self):
        for kind in ("routes", "integrations", "authorizers", "stages"):
            for action in ("GET", "POST"):
                with self.subTest(kind=kind, action=action):
                    self.assertTrue(allows("apigateway:" + action, API + "/" + kind))
            child = "$default" if kind == "stages" else "abc123"
            for action in ("GET", "PATCH"):
                self.assertTrue(allows("apigateway:" + action, f"{API}/{kind}/{child}"))

    def test_tag_write_requires_exact_ownership(self):
        correct = {"App": "journalm8", "Stage": "dev", "ManagedBy": "aws-cli"}
        self.assertTrue(allows("apigateway:POST", TAG, correct))
        for key in correct:
            for invalid in ({k: v for k, v in correct.items() if k != key},
                            dict(correct, **{key: "wrong"})):
                self.assertFalse(allows("apigateway:POST", TAG, invalid))
        self.assertFalse(allows("apigateway:POST", TAG, dict(correct, Extra="x")))
        self.assertFalse(allows("apigateway:POST", TAG))

    def test_excluded_operations_and_resources(self):
        for resource in (API + "/routes/abc", TAG):
            self.assertFalse(allows("apigateway:DELETE", resource))
        for resource in (API.replace("u06tdrfsua", "other") + "/routes",
                         API.replace("us-east-1", "us-west-2") + "/routes",
                         API + "/deployments", API + "/stages/prod", API):
            self.assertFalse(allows("apigateway:PATCH", resource))
            self.assertFalse(allows("apigateway:POST", resource))

    def test_other_environments_have_no_new_grants(self):
        for stage, region in (("prod", "us-east-1"), ("staging", "us-east-1"), ("dev", "us-west-2")):
            self.assertNotIn("WriteExactDevHttpApiTags", str(stage_policies(stage, region)))
            self.assertNotIn("ManageExactDevHttpApiChildren", str(stage_policies(stage, region)))

if __name__ == "__main__":
    unittest.main()

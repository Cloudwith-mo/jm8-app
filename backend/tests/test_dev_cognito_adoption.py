import importlib.util
from pathlib import Path
import unittest

spec = importlib.util.spec_from_file_location("adoption", Path(__file__).parents[1] / "bin/adopt_dev_cognito.py")
m = importlib.util.module_from_spec(spec)
spec.loader.exec_module(m)


class AdoptionTests(unittest.TestCase):
    def fake(self, tags=None, account=m.ACCOUNT, pool=m.POOL):
        self.calls = []
        current = dict(m.LEGACY if tags is None else tags)
        def call(service, operation, *args):
            self.calls.append(operation)
            if operation == "get-caller-identity":
                return {"Account": account}
            if operation == "describe-user-pool":
                return {"UserPool": {"Id": pool, "Name": "journalm8-dev-users", "Arn": m.ARN}}
            if operation == "list-tags-for-resource":
                return {"Tags": dict(current)}
            if operation == "tag-resource":
                import json
                current.update(json.loads(args[-1]))
                return {}
            raise AssertionError(operation)
        return call

    def test_check_does_not_write(self):
        m.execute("check", "", self.fake())
        self.assertNotIn("tag-resource", self.calls)

    def test_apply_only_tags_and_verifies(self):
        m.execute("apply", m.CONFIRM, self.fake())
        self.assertEqual(self.calls, ["get-caller-identity", "describe-user-pool", "list-tags-for-resource", "tag-resource", "list-tags-for-resource"])

    def test_idempotent(self):
        m.execute("apply", m.CONFIRM, self.fake(m.DESIRED))
        self.assertNotIn("tag-resource", self.calls)

    def test_refusals_before_write(self):
        for options in ({"account": "000000000000"}, {"pool": "other"}, {"tags": {}}, {"tags": {**m.LEGACY, "Stage": "prod"}}):
            with self.subTest(options=options):
                with self.assertRaises(ValueError):
                    m.execute("apply", m.CONFIRM, self.fake(**options))
                self.assertNotIn("tag-resource", self.calls)

    def test_confirmation(self):
        with self.assertRaises(ValueError):
            m.execute("apply", "", self.fake())
        self.assertEqual(self.calls, [])

    def test_verify_requires_adopted_tags(self):
        with self.assertRaises(ValueError):
            m.execute("verify", "", self.fake())


if __name__ == "__main__":
    unittest.main()

import json
import os
import subprocess
import sys
import tempfile
import unittest
from importlib.machinery import SourceFileLoader


HELPER_PATH = os.path.join(os.path.dirname(__file__), '..', 'bin', 'jm8_s3_helpers.py')
helpers = SourceFileLoader('jm8_s3_helpers', HELPER_PATH).load_module()


class TestJM8S3Helpers(unittest.TestCase):
    def test_merge_policy_creates_canonical_when_empty(self):
        merged = helpers.merge_bucket_policy(None, 'my-bucket')
        self.assertIn('Statement', merged)
        stmts = merged['Statement']
        self.assertEqual(stmts[0]['Sid'], 'EnforceHttpsTransport')
        self.assertEqual(stmts[0]['Effect'], 'Deny')

    def test_merge_policy_handles_policy_wrapper_and_single_statement_object(self):
        wrapped = {'Policy': json.dumps({'Version': '2012-10-17', 'Statement': {'Sid': 'Existing', 'Effect': 'Allow', 'Action': 's3:GetObject'}})}
        merged = helpers.merge_bucket_policy(wrapped, 'b')
        self.assertEqual(merged['Statement'][0]['Sid'], 'EnforceHttpsTransport')
        self.assertIn('Existing', [s.get('Sid') for s in merged['Statement']])

    def test_merge_policy_preserves_unrelated_and_replaces_stale(self):
        existing = {'Version': '2012-10-17', 'Statement': [
            {'Sid': 'Other', 'Effect': 'Allow', 'Action': 's3:GetObject', 'Resource': ['arn:foo']},
            {'Sid': 'EnforceHttpsTransport', 'Effect': 'Allow', 'Action': 's3:GetObject', 'Resource': ['arn:bad']}
        ]}
        merged = helpers.merge_bucket_policy(existing, 'b')
        self.assertEqual(merged['Statement'][0]['Sid'], 'EnforceHttpsTransport')
        sids = [s.get('Sid') for s in merged['Statement']]
        self.assertIn('Other', sids)
        jm8 = merged['Statement'][0]
        self.assertEqual(jm8['Effect'], 'Deny')
        self.assertIn('arn:aws:s3:::b', jm8['Resource'])
        self.assertIn('arn:aws:s3:::b/*', jm8['Resource'])

    def test_merge_lifecycle_replaces_managed_and_preserves_other(self):
        existing = {'Rules': [
            {'ID': 'DeleteNoncurrentVersionsAfter30Days', 'Status': 'Disabled', 'NoncurrentVersionExpiration': {'NoncurrentDays': 999}},
            {'ID': 'OtherRule', 'Status': 'Enabled', 'Expiration': {'Days': 365}}
        ]}
        merged = helpers.merge_lifecycle(existing)
        ids = {r['ID']: r for r in merged['Rules']}
        self.assertIn('AbortIncompleteMultipartAfter7Days', ids)
        self.assertIn('DeleteNoncurrentVersionsAfter30Days', ids)
        self.assertIn('OtherRule', ids)
        self.assertEqual(ids['AbortIncompleteMultipartAfter7Days']['Filter'], {'Prefix': ''})
        self.assertEqual(ids['AbortIncompleteMultipartAfter7Days']['AbortIncompleteMultipartUpload']['DaysAfterInitiation'], 7)
        self.assertEqual(ids['DeleteNoncurrentVersionsAfter30Days']['Filter'], {'Prefix': ''})
        self.assertEqual(ids['DeleteNoncurrentVersionsAfter30Days']['NoncurrentVersionExpiration']['NoncurrentDays'], 30)
        self.assertNotIn('Expiration', ids['AbortIncompleteMultipartAfter7Days'])
        self.assertNotIn('Transitions', ids['AbortIncompleteMultipartAfter7Days'])

    def test_merge_tags_preserves_unrelated_and_sets_managed(self):
        existing = {'TagSet': [{'Key': 'Owner', 'Value': 'alice'}, {'Key': 'App', 'Value': 'old'}]}
        merged = helpers.merge_tags(existing, 'myapp', 'dev')
        tagmap = {t['Key']: t['Value'] for t in merged['TagSet']}
        self.assertEqual(tagmap['Owner'], 'alice')
        self.assertEqual(tagmap['App'], 'myapp')
        self.assertEqual(tagmap['Stage'], 'dev')

    def test_decide_cors_dev_and_non_dev(self):
        self.assertIsNone(helpers.decide_cors_apply(None, 'prod'))
        desired = helpers.desired_dev_cors()
        res = helpers.decide_cors_apply(None, 'dev')
        self.assertEqual(res, desired)

    def test_load_json_empty_file_returns_none(self):
        with tempfile.NamedTemporaryFile('w', delete=False) as tf:
            tf.write('')
            path = tf.name
        try:
            self.assertIsNone(helpers.load_json(path))
        finally:
            os.unlink(path)

    def test_load_json_whitespace_returns_none(self):
        with tempfile.NamedTemporaryFile('w', delete=False) as tf:
            tf.write('  \n \t  ')
            path = tf.name
        try:
            self.assertIsNone(helpers.load_json(path))
        finally:
            os.unlink(path)

    def test_load_json_malformed_nonempty_json_raises(self):
        with tempfile.NamedTemporaryFile('w', delete=False) as tf:
            tf.write('{not valid json}')
            path = tf.name
        try:
            with self.assertRaises(json.JSONDecodeError):
                helpers.load_json(path)
        finally:
            os.unlink(path)

    def test_cli_check_error_allows_nosuch(self):
        with tempfile.NamedTemporaryFile('w', delete=False) as tf:
            tf.write('{"Error": {"Code": "NoSuchBucketPolicy"}}')
            tf.flush()
            rc = subprocess.call([sys.executable, HELPER_PATH, 'check-error', tf.name, 'NoSuchBucketPolicy'])
            os.unlink(tf.name)
            self.assertEqual(rc, 0)

    def test_cli_check_error_rejects_unexpected_accessdenied(self):
        with tempfile.NamedTemporaryFile('w', delete=False) as tf:
            tf.write('An error occurred (AccessDenied) when calling the GetObject operation')
            tf.flush()
            rc = subprocess.call([sys.executable, HELPER_PATH, 'check-error', tf.name])
            os.unlink(tf.name)
            self.assertNotEqual(rc, 0)

    def test_cli_merge_policy_accepts_empty_existing_file(self):
        with tempfile.TemporaryDirectory() as td:
            existing = os.path.join(td, 'existing.json')
            out = os.path.join(td, 'out.json')
            open(existing, 'w', encoding='utf-8').close()
            rc = subprocess.call([sys.executable, HELPER_PATH, 'merge-policy', existing, 'mybucket', out])
            self.assertEqual(rc, 0)
            with open(out, 'r', encoding='utf-8') as f:
                data = json.load(f)
            self.assertEqual(data['Statement'][0]['Sid'], 'EnforceHttpsTransport')
            self.assertEqual(data['Statement'][0]['Effect'], 'Deny')

    def test_cli_merge_policy_writes_file(self):
        with tempfile.TemporaryDirectory() as td:
            existing = os.path.join(td, 'existing.json')
            out = os.path.join(td, 'out.json')
            with open(existing, 'w') as f:
                json.dump({'Version': '2012-10-17', 'Statement': []}, f)
            rc = subprocess.call([sys.executable, HELPER_PATH, 'merge-policy', existing, 'mybucket', out])
            self.assertEqual(rc, 0)
            with open(out, 'r', encoding='utf-8') as f:
                data = json.load(f)
            self.assertIn('Statement', data)


class TestCreateResourcesScriptSafety(unittest.TestCase):
    def setUp(self):
        script_path = os.path.join(os.path.dirname(__file__), '..', 'bin', 'create-resources')
        with open(script_path, 'r', encoding='utf-8') as f:
            self.script = f.read()

    def test_no_lenient_or_true_suppression_on_dynamodb_updates(self):
        self.assertIn('update-continuous-backups', self.script)
        self.assertIn('update-table', self.script)
        self.assertNotIn('|| true', self.script)

    def test_no_ttl_or_destructive_commands(self):
        forbidden = ['update-time-to-live', 'delete-table', 'delete-bucket', 'delete-object']
        for f in forbidden:
            self.assertNotIn(f, self.script)

    def test_uses_valid_dynamodb_pitr_parameter(self):
        normalized_script = self.script.replace("\\\n", " ")
        self.assertIn(
            "--point-in-time-recovery-specification "
            "PointInTimeRecoveryEnabled=true",
            normalized_script,
        )
        self.assertNotIn(
            "--point-in-time-recovery-specification Enabled=true",
            normalized_script,
        )


if __name__ == '__main__':
    unittest.main()

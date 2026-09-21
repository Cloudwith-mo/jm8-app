# Existing dev Cognito pool adoption

Scope: account 114743615542, us-east-1, pool us-east-1_bJcMC6yDw,
name journalm8-dev-users. This is a deliberate move to the active jm8-app
AWS CLI maintenance path, not a Terraform/CloudFormation import.

The retired journalm8 Terraform backend has a July 26, 2026 delete marker
and April state versions. Do not restore those versions or apply that old
configuration against this pool without a separate ownership review.

The reviewed legacy tags are Environment=dev, Project=journalm8, Owner=mko,
ManagedBy=Terraform. Adoption preserves the first three, changes ManagedBy
to aws-cli and adds App=journalm8 and Stage=dev. No IAM policy is edited.
Existing backend/bin/jm8_resource_tags.sh maintains those canonical tags.

Security effect: the existing tag-conditioned deployer policy now permits
DescribeUserPool, DescribeUserPoolClient, ListTagsForResource,
ListUserPoolClients, CreateUserPoolClient, CreateUserPoolDomain,
GetUICustomization, SetUICustomization, and conditional TagResource on this
pool. Review the deployed policy for drift before mutation. Adoption does
not call any client, domain, branding, password, schema, user, or pool
configuration mutation. Later deployments are separate operations.

After review and merge, from the repository root:

```bash
python3 backend/bin/adopt_dev_cognito.py check
```

Only after the read-only check and explicit operator approval:

```bash
python3 backend/bin/adopt_dev_cognito.py apply --confirmation adopt-journalm8-dev-cognito
python3 backend/bin/adopt_dev_cognito.py verify
```

The tool uses the configured jm8-dev AWS profile and refuses a different
account, pool identity, unknown tags, or missing confirmation. AWS errors
stop execution; do not grant broader permissions or retry blindly.
It prints only pool identity and mode, not the full pool document or secrets.
Successful apply is idempotent. Unexpected post-write tags require review.

Rollback is a separate approved operation after pausing deployments:
verify the pool and current tags, restore ManagedBy=Terraform and remove
only App and Stage. Preserve Environment, Project, Owner. This revokes the
tag-conditioned deployment access but does not reverse any later deployment
changes or restore Terraform ownership. Never delete/recreate the pool or
restore the old state as a tag rollback.

Local verification (no AWS calls):

```bash
python3 -m unittest discover -s backend/tests -p test_dev_cognito_adoption.py -v
```

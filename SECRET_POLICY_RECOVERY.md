# Dev/staging secret policy boundary recovery plan

Status: source correction only. No AWS mutation or secret-value access performed.

Confirmed live defect: the dev GitHub deployer secrets managed policy targets
journalm8/prod/stripe-* and its create condition uses journalm8/prod/stripe.
Staging is affected by the same generator; its live policy must still be inspected.
This is permission exposure, not evidence that a secret was accessed.

## Scope and gates

1. Hold dev/staging application deployment retries. Review and merge this PR
   after CI passes. Use the merged exact commit for template generation.
2. With the configured provisioning profile jm8-dev, verify account
   114743615542 and region us-east-1. Inspect both role stacks, all managed
   policy attachments, inline policies and default documents. Check single-role
   attachment scope and any unexpected boundaries or resource policies.
   Never invoke GetSecretValue to test access.
3. Read the existing CloudFormation templates and compare them with locally
   generated templates. Generate mode of provision-github-oidc-deployers makes
   no AWS calls. Do NOT use its apply mode for recovery: its equality preflight
   rejects changed policy documents, and its deployment loop includes prod.
4. Prepare two UPDATE change sets, one per existing stack:
   journalm8-dev-github-deployer and journalm8-staging-github-deployer.
   Preserve stack parameters, tags, role settings, and other properties.
   Expected changes: ONLY PolicyDevSecrets / PolicyStagingSecrets PolicyDocument,
   with Resource and secretsmanager:Name corrected to the matching stage.
   Require no replacement, additions, deletions, action expansion, other policy
   changes, trust changes, role changes, or provider/prod changes. Stop on drift.
5. Review the exact change-set IDs and obtain execution approval. Execute dev
   first, wait for UPDATE_COMPLETE, verify its policy, then execute the separately
   approved staging change set. On errors stop and inspect stack events; do not
   modify IAM directly or grant broader rights to bypass a failure.

## Verification before deployment resumes

- Re-read actual default policy documents and compare to corrected generated
  documents, including exact secret resource and create-name condition.
- Inspect all policies attached to each role; ensure no other identity policy
  grants access to another stage's secret paths.
- Use IAM policy simulation for DescribeSecret, GetSecretValue, PutSecretValue,
  and TagResource: own-stage exact secret ARN should be allowed; other-stage
  ARNs must not be allowed. Inspect every result and missing context. For
  CreateSecret include the exact name and App/Stage request-tag context and test
  both own-stage and wrong-stage cases. Simulation does not retrieve secrets and
  is not proof of all effective access; account for resource policies and other
  controls in the review. Do not attempt actual reads or writes of secret values.
- Re-run provision-github-oidc-deployers verify only after both stacks match.
- Production generated policies must remain unchanged. Production stack and
  OIDC provider must not be update targets.

## Failure handling and follow-up

Do not restore the vulnerable policy as a routine rollback. CloudFormation
rollback can restore the old exposure: inspect default policies after any failed
update and keep deployment retries paused. Prepare a separately approved
containment/forward-fix if needed. A review of available audit logs for historical
cross-stage secret use can be performed separately without reading credentials;
do not infer misuse from the policy defect alone. Credential rotation is a
separate decision, not part of this tag/path correction.

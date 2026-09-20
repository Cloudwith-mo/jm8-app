# JM8 GitHub OIDC bootstrap

The staged deployment workflow requires three stage-isolated AWS roles and the
GitHub Actions OpenID Connect provider. The bootstrap uses one small provider
stack and one stack per stage so creation and updates remain reviewable,
repeatable, and below CloudFormation's direct template size limit. It never
creates long-lived AWS keys.

## Security boundary

Each role trusts only one exact GitHub Environment subject:

| GitHub Environment | AWS role | Required OIDC subject |
| --- | --- | --- |
| `jm8-dev` | `journalm8-dev-github-deployer` | `repo:Cloudwith-mo/jm8-app:environment:jm8-dev` |
| `jm8-staging` | `journalm8-staging-github-deployer` | `repo:Cloudwith-mo/jm8-app:environment:jm8-staging` |
| `jm8-prod` | `journalm8-prod-github-deployer` | `repo:Cloudwith-mo/jm8-app:environment:jm8-prod` |

All trusts require audience `sts.amazonaws.com`. Each role receives six
stage-specific managed policies generated from the existing reviewed production
deployment inventory. No role receives wildcard actions, administrator policy,
inline policy, cross-stage resources, or permission to modify itself.

The IAM API requires an OIDC thumbprint value. The template records GitHub's
published DigiCert root thumbprint; IAM normally validates GitHub against its
trusted root certificate library and uses the configured thumbprint as a
fallback. Review this value against current AWS/GitHub guidance before apply.

## Generate and review (no AWS mutation)

```bash
cd "$HOME/jm8-app/backend"
export AWS_PROFILE=jm8-dev
export AWS_REGION=us-east-1
export EXPECTED_AWS_ACCOUNT_ID=114743615542
./bin/provision-github-oidc-deployers generate
git diff --check
```

Review `.build/github-oidc-deployers/manifest.json` and the four generated
`github-oidc-*.template.json` files.

## Apply (AWS mutation)

Only run this after the PR containing this bootstrap has merged and a fresh
read-only inspection still shows the provider and all three roles absent.
The caller needs CloudFormation and the narrowly relevant IAM bootstrap
permissions. An `AccessDenied` response is a stop condition; do not broaden an
existing application deployer role to work around it.

```bash
cd "$HOME/jm8-app/backend"
export AWS_PROFILE=jm8-dev
export AWS_REGION=us-east-1
export EXPECTED_AWS_ACCOUNT_ID=114743615542
export GITHUB_OIDC_BOOTSTRAP_CONFIRMATION=create-github-oidc-deployers
./bin/provision-github-oidc-deployers apply
unset GITHUB_OIDC_BOOTSTRAP_CONFIRMATION
```

On the first apply, the command fails closed if the GitHub provider or any
target role already exists outside its stack. On later applies, CloudFormation
reconciles its own stacks. A partial bootstrap is a stop condition requiring
stack-event inspection before retrying. The command verifies the account, completed stacks,
provider, exact trust documents, role tags, absence of inline policies, exact
managed-policy attachments, policy documents, and single-role attachment scope.

## Connect GitHub Environments

After verification succeeds, set each environment's `AWS_DEPLOY_ROLE_ARN` to
the corresponding role ARN printed in the generated manifest. Keep account ID
and region as environment variables, never secrets. Then dispatch the staged
workflow to `dev` first using an exact commit from `main`.

Verification can be repeated without mutation:

```bash
./bin/provision-github-oidc-deployers verify
```

Do not delete these stacks as a routine rollback: deletion removes deployment
roles or the account-level GitHub OIDC provider. Application
rollback remains a forward redeployment of a previously verified commit.

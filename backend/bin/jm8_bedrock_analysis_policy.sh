#!/usr/bin/env bash

JM8_BEDROCK_POLICY_HELPER_DIR="$(
  cd "$(dirname "${BASH_SOURCE[0]}")" && pwd
)"

jm8_bedrock_analysis_role_name() {
  printf '%s' "${APP_NAME}-${STAGE}-lambda-basic-role"
}

jm8_bedrock_analysis_policy_name() {
  printf '%s' "${APP_NAME}-${STAGE}-bedrock-analysis"
}

jm8_reconcile_bedrock_analysis_policy() {
  local model_id="$1"
  local build_dir="$2"

  : "${AWS_PROFILE:?AWS_PROFILE is required.}"
  : "${AWS_REGION:?AWS_REGION is required.}"
  : "${APP_NAME:?APP_NAME is required.}"
  : "${STAGE:?STAGE is required.}"
  : "${EXPECTED_AWS_ACCOUNT_ID:?EXPECTED_AWS_ACCOUNT_ID is required.}"

  case "$STAGE" in
    dev|staging|prod)
      ;;
    *)
      echo "Stage must be dev, staging, or prod." >&2
      return 1
      ;;
  esac

  if [[ ! "$APP_NAME" =~ ^[a-z0-9-]+$ ]]; then
    echo "Application name is malformed." >&2
    return 1
  fi

  local caller_arn
  local aws_partition
  local caller_account
  local expected_role_arn
  local actual_role_arn
  local profile_response_file
  local policy_file
  local applied_policy_file

  JM8_BEDROCK_ANALYSIS_ROLE_NAME="$(jm8_bedrock_analysis_role_name)"
  JM8_BEDROCK_ANALYSIS_POLICY_NAME="$(jm8_bedrock_analysis_policy_name)"

  mkdir -p "$build_dir"
  profile_response_file="${build_dir}/inference-profile.json"
  policy_file="${build_dir}/bedrock-analysis-policy.json"
  applied_policy_file="${build_dir}/bedrock-analysis-policy-applied.json"

  caller_arn="$(
    aws sts get-caller-identity \
      --profile "$AWS_PROFILE" \
      --region "$AWS_REGION" \
      --query "Arn" \
      --output text
  )"

  if [[ ! "$caller_arn" =~ ^arn:([a-z0-9-]+):(iam|sts)::([0-9]{12}): ]]; then
    echo "AWS caller identity response is malformed." >&2
    return 1
  fi

  aws_partition="${BASH_REMATCH[1]}"
  caller_account="${BASH_REMATCH[3]}"
  if [ "$caller_account" != "$EXPECTED_AWS_ACCOUNT_ID" ]; then
    echo "AWS caller account does not match the expected deployment account." >&2
    return 1
  fi

  expected_role_arn="arn:${aws_partition}:iam::${caller_account}:role/${JM8_BEDROCK_ANALYSIS_ROLE_NAME}"
  actual_role_arn="$(
    aws iam get-role \
      --role-name "$JM8_BEDROCK_ANALYSIS_ROLE_NAME" \
      --profile "$AWS_PROFILE" \
      --query "Role.Arn" \
      --output text
  )"

  if [ "$actual_role_arn" != "$expected_role_arn" ]; then
    echo "Bedrock analyzer API role ARN is missing or incorrect." >&2
    return 1
  fi

  echo "Resolving Bedrock inference profile."
  aws bedrock get-inference-profile \
    --inference-profile-identifier "$model_id" \
    --profile "$AWS_PROFILE" \
    --region "$AWS_REGION" \
    --output json \
    > "$profile_response_file"

  python3 \
    "$JM8_BEDROCK_POLICY_HELPER_DIR/jm8_bedrock_analysis_policy.py" \
    generate \
    "$profile_response_file" \
    "$policy_file" \
    "$model_id" \
    "$aws_partition" \
    "$AWS_REGION" \
    "$caller_account" \
    "$APP_NAME" \
    "$STAGE"

  echo "Reconciling the stage-specific Bedrock analyzer policy."
  aws iam put-role-policy \
    --role-name "$JM8_BEDROCK_ANALYSIS_ROLE_NAME" \
    --policy-name "$JM8_BEDROCK_ANALYSIS_POLICY_NAME" \
    --policy-document "file://${policy_file}" \
    --profile "$AWS_PROFILE"

  aws iam get-role-policy \
    --role-name "$JM8_BEDROCK_ANALYSIS_ROLE_NAME" \
    --policy-name "$JM8_BEDROCK_ANALYSIS_POLICY_NAME" \
    --profile "$AWS_PROFILE" \
    --output json \
    > "$applied_policy_file"

  python3 \
    "$JM8_BEDROCK_POLICY_HELPER_DIR/jm8_bedrock_analysis_policy.py" \
    verify \
    "$applied_policy_file" \
    "$policy_file" \
    "$JM8_BEDROCK_ANALYSIS_ROLE_NAME" \
    "$JM8_BEDROCK_ANALYSIS_POLICY_NAME" \
    "$model_id" \
    "$aws_partition" \
    "$AWS_REGION" \
    "$caller_account" \
    "$APP_NAME" \
    "$STAGE"

  echo "Bedrock analyzer policy verified."
}

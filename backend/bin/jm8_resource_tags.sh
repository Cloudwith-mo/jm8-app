#!/usr/bin/env bash

JM8_RESOURCE_TAG_SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
JM8_RESOURCE_TAG_HELPER="${JM8_RESOURCE_TAG_SCRIPT_DIR}/jm8_resource_tag_contract.py"

jm8_require_resource_tag_context() {
  : "${AWS_PROFILE:?AWS_PROFILE is required for resource tag reconciliation.}"
  : "${AWS_REGION:?AWS_REGION is required for resource tag reconciliation.}"
  : "${EXPECTED_AWS_ACCOUNT_ID:?EXPECTED_AWS_ACCOUNT_ID is required for resource tag reconciliation.}"
  : "${APP_NAME:?APP_NAME is required for resource tag reconciliation.}"
  : "${STAGE:?STAGE is required for resource tag reconciliation.}"

  case "$STAGE" in
    dev|staging|prod) ;;
    *)
      echo "Resource tag stage is invalid." >&2
      return 1
      ;;
  esac

  if [ "$APP_NAME" != "journalm8" ]; then
    echo "Resource tag application name is not approved." >&2
    return 1
  fi

  if [ "$STAGE" = "prod" ] && \
     [ "$EXPECTED_AWS_ACCOUNT_ID" != "114743615542" ]; then
    echo "Production resource tag context is not approved." >&2
    return 1
  fi
}

jm8_tag_contract_command() {
  local command_name="$1"
  shift
  python3 "$JM8_RESOURCE_TAG_HELPER" \
    "$command_name" \
    --app-name "$APP_NAME" \
    --stage "$STAGE" \
    --account-id "$EXPECTED_AWS_ACCOUNT_ID" \
    --region "$AWS_REGION" \
    "$@"
}

jm8_resolve_http_api_id() {
  local expected_name="$1"
  local inventory

  jm8_require_resource_tag_context
  if [ "$expected_name" != "${APP_NAME}-${STAGE}-api" ]; then
    echo "HTTP API name does not match the stage contract." >&2
    return 1
  fi

  inventory="$(aws apigatewayv2 get-apis \
    --profile "$AWS_PROFILE" \
    --region "$AWS_REGION" \
    --output json)"
  printf '%s' "$inventory" | jm8_tag_contract_command \
    resolve-api \
    --expected-name "$expected_name"
}

jm8_verify_http_api_tags() {
  local api_id="$1"
  local expected_name="$2"
  local resource_arn
  local api_document
  local tag_document

  jm8_require_resource_tag_context
  api_document="$(aws apigatewayv2 get-api \
    --api-id "$api_id" \
    --profile "$AWS_PROFILE" \
    --region "$AWS_REGION" \
    --output json)"
  if ! printf '%s' "$api_document" | jm8_tag_contract_command \
    validate-api \
    --expected-name "$expected_name" \
    --resource-id "$api_id"
  then
    return 1
  fi

  if [ "$STAGE" = "prod" ]; then
    if ! printf '%s' "$api_document" | jm8_tag_contract_command \
      verify-exact-tags
    then
      return 1
    fi
    return 0
  fi

  resource_arn="arn:aws:apigateway:${AWS_REGION}::/apis/${api_id}"
  tag_document="$(aws apigatewayv2 get-tags \
    --resource-arn "$resource_arn" \
    --profile "$AWS_PROFILE" \
    --region "$AWS_REGION" \
    --output json)"
  printf '%s' "$tag_document" | jm8_tag_contract_command verify-tags
}

jm8_reconcile_http_api_tags() {
  local api_id="$1"
  local expected_name="$2"
  local resource_arn
  local api_document
  local tag_document

  jm8_require_resource_tag_context
  if [ "$expected_name" != "${APP_NAME}-${STAGE}-api" ]; then
    echo "HTTP API name does not match the stage contract." >&2
    return 1
  fi

  api_document="$(aws apigatewayv2 get-api \
    --api-id "$api_id" \
    --profile "$AWS_PROFILE" \
    --region "$AWS_REGION" \
    --output json)"
  if ! printf '%s' "$api_document" | jm8_tag_contract_command \
    validate-api \
    --expected-name "$expected_name" \
    --resource-id "$api_id"
  then
    return 1
  fi

  if [ "$STAGE" = "prod" ]; then
    if ! printf '%s' "$api_document" | jm8_tag_contract_command \
      verify-exact-tags
    then
      return 1
    fi
    return 0
  fi

  resource_arn="arn:aws:apigateway:${AWS_REGION}::/apis/${api_id}"
  tag_document="$(aws apigatewayv2 get-tags \
    --resource-arn "$resource_arn" \
    --profile "$AWS_PROFILE" \
    --region "$AWS_REGION" \
    --output json)"
  printf '%s' "$tag_document" | jm8_tag_contract_command \
    verify-tags-before-reconcile

  aws apigatewayv2 tag-resource \
    --resource-arn "$resource_arn" \
    --tags \
      "{\"App\":\"${APP_NAME}\",\"Stage\":\"${STAGE}\",\"ManagedBy\":\"aws-cli\"}" \
    --profile "$AWS_PROFILE" \
    --region "$AWS_REGION" \
    >/dev/null

  jm8_verify_http_api_tags "$api_id" "$expected_name"
}

jm8_resolve_cognito_user_pool_id() {
  local expected_name="$1"
  local inventory

  jm8_require_resource_tag_context
  if [ "$expected_name" != "${APP_NAME}-${STAGE}-users" ]; then
    echo "Cognito user-pool name does not match the stage contract." >&2
    return 1
  fi
  inventory="$(aws cognito-idp list-user-pools \
    --max-results 60 \
    --profile "$AWS_PROFILE" \
    --region "$AWS_REGION" \
    --output json)"
  printf '%s' "$inventory" | jm8_tag_contract_command \
    resolve-user-pool \
    --expected-name "$expected_name"
}

jm8_reconcile_cognito_user_pool_tags() {
  local pool_id="$1"
  local expected_name="$2"
  local pool_document
  local pool_arn
  local tag_document

  jm8_require_resource_tag_context
  pool_document="$(aws cognito-idp describe-user-pool \
    --user-pool-id "$pool_id" \
    --profile "$AWS_PROFILE" \
    --region "$AWS_REGION" \
    --output json)"
  pool_arn="$(printf '%s' "$pool_document" | jm8_tag_contract_command \
    validate-user-pool \
    --expected-name "$expected_name" \
    --resource-id "$pool_id")"

  tag_document="$(aws cognito-idp list-tags-for-resource \
    --resource-arn "$pool_arn" \
    --profile "$AWS_PROFILE" \
    --region "$AWS_REGION" \
    --output json)"
  printf '%s' "$tag_document" | jm8_tag_contract_command \
    verify-tags-before-reconcile

  aws cognito-idp tag-resource \
    --resource-arn "$pool_arn" \
    --tags \
      "{\"App\":\"${APP_NAME}\",\"Stage\":\"${STAGE}\",\"ManagedBy\":\"aws-cli\"}" \
    --profile "$AWS_PROFILE" \
    --region "$AWS_REGION" \
    >/dev/null

  tag_document="$(aws cognito-idp list-tags-for-resource \
    --resource-arn "$pool_arn" \
    --profile "$AWS_PROFILE" \
    --region "$AWS_REGION" \
    --output json)"
  printf '%s' "$tag_document" | jm8_tag_contract_command verify-tags
}

jm8_reconcile_lambda_tags() {
  local function_name="$1"
  local function_document
  local function_arn
  local tag_document

  jm8_require_resource_tag_context
  case "$function_name" in
    "${APP_NAME}-${STAGE}-api" | \
    "${APP_NAME}-${STAGE}-ocr-worker" | \
    "${APP_NAME}-${STAGE}-ocr-failure-handler" | \
    "${APP_NAME}-${STAGE}-historical-reanalysis-worker" | \
    "${APP_NAME}-${STAGE}-historical-reanalysis-coordinator") ;;
    *)
      echo "Lambda name does not match the stage contract." >&2
      return 1
      ;;
  esac

  function_document="$(aws lambda get-function \
    --function-name "$function_name" \
    --profile "$AWS_PROFILE" \
    --region "$AWS_REGION" \
    --output json)"
  function_arn="$(printf '%s' "$function_document" | jm8_tag_contract_command \
    validate-lambda \
    --expected-name "$function_name")"

  tag_document="$(aws lambda list-tags \
    --resource "$function_arn" \
    --profile "$AWS_PROFILE" \
    --region "$AWS_REGION" \
    --output json)"
  printf '%s' "$tag_document" | jm8_tag_contract_command \
    verify-tags-before-reconcile

  aws lambda tag-resource \
    --resource "$function_arn" \
    --tags \
      "{\"App\":\"${APP_NAME}\",\"Stage\":\"${STAGE}\",\"ManagedBy\":\"aws-cli\"}" \
    --profile "$AWS_PROFILE" \
    --region "$AWS_REGION" \
    >/dev/null

  tag_document="$(aws lambda list-tags \
    --resource "$function_arn" \
    --profile "$AWS_PROFILE" \
    --region "$AWS_REGION" \
    --output json)"
  printf '%s' "$tag_document" | jm8_tag_contract_command verify-tags
}

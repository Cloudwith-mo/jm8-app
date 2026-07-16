import json
import os
import uuid

import boto3


stepfunctions = boto3.client("stepfunctions")


def start_ocr_execution(
    user_id: str,
    entry_id: str,
    force: bool = False,
) -> dict:
    workflow_arn = os.environ.get("OCR_WORKFLOW_ARN", "").strip()

    if not workflow_arn:
        raise RuntimeError(
            "OCR_WORKFLOW_ARN is not configured on the API Lambda."
        )

    execution_name = (
        f"ocr-{entry_id}-{uuid.uuid4().hex[:12]}"
    )[:80]

    workflow_input = {
        "userId": user_id,
        "entryId": entry_id,
        "force": force,
    }

    result = stepfunctions.start_execution(
        stateMachineArn=workflow_arn,
        name=execution_name,
        input=json.dumps(workflow_input),
    )

    return {
        "executionArn": result["executionArn"],
        "executionName": execution_name,
        "startedAt": result["startDate"].isoformat(),
    }

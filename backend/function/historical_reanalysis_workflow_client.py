import json
import os

import boto3
from botocore.exceptions import ClientError


stepfunctions = boto3.client(
    "stepfunctions"
)


def start_historical_reanalysis_execution(
    *,
    user_id: str,
    job_id: str,
) -> dict:
    workflow_arn = os.environ.get(
        "HISTORICAL_REANALYSIS_WORKFLOW_ARN",
        "",
    ).strip()

    if not workflow_arn:
        raise RuntimeError(
            "HISTORICAL_REANALYSIS_WORKFLOW_ARN "
            "is not configured on the API Lambda."
        )

    workflow_input = {
        "userId": user_id,
        "jobId": job_id,
        "cursor": None,
    }

    try:
        result = (
            stepfunctions.start_execution(
                stateMachineArn=workflow_arn,
                name=job_id,
                input=json.dumps(
                    workflow_input,
                    separators=(",", ":"),
                ),
            )
        )

    except ClientError as exc:
        error_code = str(
            exc.response.get(
                "Error",
                {},
            ).get(
                "Code",
                "",
            )
        )

        if (
            error_code
            == "ExecutionAlreadyExists"
        ):
            return {
                "executionName": job_id,
                "duplicate": True,
            }

        raise

    return {
        "executionName": job_id,
        "duplicate": False,
        "started": bool(
            result.get("executionArn")
        ),
    }

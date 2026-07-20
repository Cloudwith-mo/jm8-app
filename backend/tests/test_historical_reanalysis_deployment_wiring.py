from pathlib import Path
import unittest


BACKEND_ROOT = (
    Path(__file__)
    .resolve()
    .parents[1]
)


def read_backend_file(
    relative_path: str,
) -> str:
    return (
        BACKEND_ROOT
        .joinpath(relative_path)
        .read_text()
    )


class HistoricalReanalysisDeploymentTests(
    unittest.TestCase
):
    def test_api_deploy_resolves_workflow(
        self,
    ):
        script = read_backend_file(
            "bin/deploy"
        )

        self.assertIn(
            (
                "HISTORICAL_REANALYSIS_"
                "WORKFLOW_NAME"
            ),
            script,
        )
        self.assertIn(
            (
                "HISTORICAL_REANALYSIS_"
                "WORKFLOW_ARN"
            ),
            script,
        )
        self.assertIn(
            (
                "start-historical-"
                "reanalysis-workflow"
            ),
            script,
        )
        self.assertIn(
            "states:StartExecution",
            script,
        )

    def test_api_environment_has_workflow_arn(
        self,
    ):
        script = read_backend_file(
            "bin/deploy"
        )

        marker = (
            "HISTORICAL_REANALYSIS_"
            "WORKFLOW_ARN="
            "${HISTORICAL_REANALYSIS_"
            "WORKFLOW_ARN}"
        )

        self.assertEqual(
            script.count(marker),
            2,
        )

    def test_create_api_has_job_routes(
        self,
    ):
        script = read_backend_file(
            "bin/create-api"
        )

        self.assertIn(
            (
                'create_route_if_missing '
                '"POST /analysis/'
                'reanalysis/jobs"'
            ),
            script,
        )
        self.assertIn(
            (
                'create_route_if_missing '
                '"GET /analysis/'
                'reanalysis/jobs/{jobId}"'
            ),
            script,
        )
        self.assertIn(
            '"authorization"',
            script,
        )
        self.assertIn(
            "update-api",
            script,
        )

    def test_secure_api_has_job_routes(
        self,
    ):
        script = read_backend_file(
            "bin/secure-api"
        )

        self.assertIn(
            (
                'secure_route "POST '
                '/analysis/reanalysis/jobs"'
            ),
            script,
        )
        self.assertIn(
            (
                'secure_route "GET '
                '/analysis/reanalysis/'
                'jobs/{jobId}"'
            ),
            script,
        )

    def test_transaction_roles_can_put_items(
        self,
    ):
        script = read_backend_file(
            "bin/"
            "deploy-historical-reanalysis-workflow"
        )

        self.assertEqual(
            script.count(
                '"dynamodb:PutItem"'
            ),
            2,
        )


if __name__ == "__main__":
    unittest.main()

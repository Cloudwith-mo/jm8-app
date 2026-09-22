import contextlib
import importlib.util
import io
import sys
import types
import unittest
from pathlib import Path
from unittest.mock import Mock, patch


class DevIosAuthConfigurationTest(unittest.TestCase):
    def test_apply_is_additive_and_never_updates_api_cors(self):
        client = {"UserPoolId": "us-east-1_bJcMC6yDw", "ClientId": "4t37mcfdkg5gdvl7ev8vt91ojg",
                  "ClientName": "journalm8-dev-web", "AllowedOAuthFlows": ["code"],
                  "AllowedOAuthFlowsUserPoolClient": True,
                  "CallbackURLs": ["http://localhost:5173/"], "LogoutURLs": ["http://localhost:5173/"],
                  "RefreshTokenValidity": 30}
        cognito, gateway, sts = Mock(), Mock(), Mock()
        sts.get_caller_identity.return_value = {"Account": "114743615542"}
        cognito.describe_user_pool.return_value = {"UserPool": {"Name": "journalm8-dev-users", "Domain": "journalm8-dev-114743615542"}}
        gateway.get_api.return_value = {"Name": "journalm8-dev-api"}
        cognito.describe_user_pool_client.side_effect = lambda **kw: {"UserPoolClient": dict(client)}
        cognito.meta.service_model.operation_model.return_value.input_shape.members = dict(client)
        cognito.update_user_pool_client.side_effect = lambda **kw: client.update(kw)
        session = Mock()
        session.client.side_effect = lambda name: {"sts": sts, "cognito-idp": cognito, "apigatewayv2": gateway}[name]
        boto = types.SimpleNamespace(Session=Mock(return_value=session))
        spec = importlib.util.spec_from_file_location("ios_auth_test_target", Path(__file__).resolve().parents[1] / "bin/configure_dev_ios_auth.py")
        module = importlib.util.module_from_spec(spec)
        with patch.dict(sys.modules, {"boto3": boto}), patch.object(sys, "argv", ["helper", "--apply"]), contextlib.redirect_stdout(io.StringIO()):
            spec.loader.exec_module(module)
            module.main()
            module.main()  # Retry after partial completion must remain idempotent.
        self.assertEqual(client["CallbackURLs"], ["http://localhost:5173/", module.CALLBACK])
        self.assertEqual(client["LogoutURLs"], ["http://localhost:5173/"])
        self.assertEqual(client["RefreshTokenValidity"], 30)
        gateway.update_api.assert_not_called()

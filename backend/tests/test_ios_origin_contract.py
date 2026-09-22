import sys
from pathlib import Path
import unittest
sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "bin"))
from jm8_environment_contract import EnvironmentContractError, validate_allowed_origins


class IosOriginContractTest(unittest.TestCase):
    def test_api_gateway_rejects_custom_scheme_even_in_dev(self):
        with self.assertRaises(EnvironmentContractError):
            validate_allowed_origins("dev", "http://localhost:5173,capacitor://localhost")

    def test_reject_release_and_similar_origins(self):
        for stage, origin in [("staging", "capacitor://localhost"), ("prod", "capacitor://localhost"), *[("dev", item) for item in ["capacitor://evil.test", "capacitor://localhost/path", "capacitor://localhost:5173", "capacitor://localhost?x=1", "capacitor://localhost,capacitor://localhost"]]]:
            with self.subTest(stage=stage, origin=origin), self.assertRaises(EnvironmentContractError):
                validate_allowed_origins(stage, origin)

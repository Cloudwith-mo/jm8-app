#!/usr/bin/env python3
"""Review or apply additive authentication settings for the fixed dev iOS app."""
import argparse
import json
import boto3

ACCOUNT = "114743615542"
POOL = "us-east-1_bJcMC6yDw"
CLIENT = "4t37mcfdkg5gdvl7ev8vt91ojg"
API = "u06tdrfsua"
CALLBACK = "com.cloudwithmo.journalm8.dev://auth/callback"
ORIGIN = "capacitor://localhost"


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--apply", action="store_true", help="Apply the displayed dev-only additions")
    args = parser.parse_args()
    session = boto3.Session(profile_name="jm8-dev", region_name="us-east-1")
    if session.client("sts").get_caller_identity()["Account"] != ACCOUNT:
        raise SystemExit("STOP: unexpected AWS account")
    cognito = session.client("cognito-idp")
    gateway = session.client("apigatewayv2")
    pool = cognito.describe_user_pool(UserPoolId=POOL)["UserPool"]
    api = gateway.get_api(ApiId=API)
    client = cognito.describe_user_pool_client(UserPoolId=POOL, ClientId=CLIENT)["UserPoolClient"]
    if (pool["Name"] != "journalm8-dev-users"
            or pool.get("Domain") != "journalm8-dev-114743615542"
            or api["Name"] != "journalm8-dev-api"
            or client["ClientName"] != "journalm8-dev-web"
            or client.get("ClientSecret")
            or client.get("AllowedOAuthFlows") != ["code"]
            or client.get("AllowedOAuthFlowsUserPoolClient") is not True):
        raise SystemExit("STOP: development authentication resource mismatch")
    callbacks = list(dict.fromkeys([*client.get("CallbackURLs", []), CALLBACK]))
    cors = dict(api.get("CorsConfiguration", {}))
    cors["AllowOrigins"] = list(dict.fromkeys([*cors.get("AllowOrigins", []), ORIGIN]))
    if "*" in cors["AllowOrigins"]:
        raise SystemExit("STOP: wildcard CORS requires separate review")
    print(json.dumps({"stage": "dev", "api": API, "client": CLIENT,
                      "CallbackURLs": callbacks, "CorsConfiguration": cors,
                      "apply": args.apply}, indent=2))
    if not args.apply:
        return
    allowed = cognito.meta.service_model.operation_model("UpdateUserPoolClient").input_shape.members
    payload = {key: value for key, value in client.items() if key in allowed}
    payload.update(UserPoolId=POOL, ClientId=CLIENT, CallbackURLs=callbacks)
    cognito.update_user_pool_client(**payload)
    gateway.update_api(ApiId=API, CorsConfiguration=cors)
    actual_client = cognito.describe_user_pool_client(UserPoolId=POOL, ClientId=CLIENT)["UserPoolClient"]
    actual_cors = gateway.get_api(ApiId=API).get("CorsConfiguration", {})
    if set(actual_client.get("CallbackURLs", [])) != set(callbacks):
        raise SystemExit("STOP: callback readback mismatch")
    if set(actual_cors.get("AllowOrigins", [])) != set(cors["AllowOrigins"]):
        raise SystemExit("STOP: CORS readback mismatch")
    print("Verified development callback and CORS additions.")
    print("Deployment ALLOWED_ORIGINS: " + ",".join(cors["AllowOrigins"]))


if __name__ == "__main__":
    main()

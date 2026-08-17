import test from "node:test";
import assert from "node:assert/strict";

import {
  getApprovedModeLocalFiles,
  isApprovedModeSpecificEnvSource,
  validateFrontendEnv,
} from "./frontend_env_contract.mjs";

function baseEnv(overrides = {}) {
  return {
    VITE_APP_STAGE: "dev",
    VITE_API_ENDPOINT: "http://localhost:3000",
    VITE_COGNITO_DOMAIN: "http://localhost:4000",
    VITE_COGNITO_CLIENT_ID: "client-dev-123",
    VITE_COGNITO_REDIRECT_URI: "http://localhost:5173/callback",
    VITE_COGNITO_LOGOUT_URI: "http://localhost:5173",
    ...overrides,
  };
}

test("rejects missing required variables", () => {
  assert.throws(
    () => validateFrontendEnv(baseEnv({ VITE_API_ENDPOINT: "" }), { mode: "development" }),
    /Missing required frontend env variables: VITE_API_ENDPOINT/
  );
});

test("rejects localhost endpoints for staging", () => {
  assert.throws(
    () =>
      validateFrontendEnv(
        baseEnv({
          VITE_APP_STAGE: "staging",
          VITE_API_ENDPOINT: "http://localhost:3000",
          VITE_COGNITO_DOMAIN: "https://staging-auth.example.com",
          VITE_COGNITO_CLIENT_ID: "client-staging-123",
          VITE_COGNITO_REDIRECT_URI: "https://staging.example.com/callback",
          VITE_COGNITO_LOGOUT_URI: "https://staging.example.com",
        }),
        { mode: "staging" }
      ),
    /must use https for staging\/prod/
  );
});

test("rejects stage mismatch between mode and VITE_APP_STAGE", () => {
  assert.throws(
    () => validateFrontendEnv(baseEnv({ VITE_APP_STAGE: "prod" }), { mode: "development" }),
    /VITE_APP_STAGE must be 'dev' for mode 'development'/
  );
});

test("errors never include concrete secret values", () => {
  const secretLikeValue = ["sk", "test", "never", "show", "this"].join("_");

  let message = "";
  try {
    validateFrontendEnv(baseEnv({ VITE_API_ENDPOINT: secretLikeValue }), { mode: "development" });
  } catch (error) {
    message = String(error?.message || "");
  }

  assert.ok(message.length > 0);
  assert.equal(message.includes(secretLikeValue), false);
});

test(".env.local is not an approved mode-specific source", () => {
  const approved = getApprovedModeLocalFiles("development");
  assert.equal(approved.includes(".env.local"), false);
  assert.equal(isApprovedModeSpecificEnvSource(".env.local", "development"), false);
  assert.equal(isApprovedModeSpecificEnvSource(".env.development.local", "development"), true);
});

test("staging rejects legacy .env.local presence", () => {
  assert.throws(
    () =>
      validateFrontendEnv(
        baseEnv({
          VITE_APP_STAGE: "staging",
          VITE_API_ENDPOINT: "https://api.staging.example.com",
          VITE_COGNITO_DOMAIN: "https://staging-auth.example.com",
          VITE_COGNITO_CLIENT_ID: "client-staging-123",
          VITE_COGNITO_REDIRECT_URI: "https://staging.example.com/callback",
          VITE_COGNITO_LOGOUT_URI: "https://staging.example.com",
        }),
        { mode: "staging", hasLegacyEnvLocal: true }
      ),
    /Legacy \.env\.local is not allowed/
  );
});

test("production rejects legacy .env.local presence", () => {
  assert.throws(
    () =>
      validateFrontendEnv(
        baseEnv({
          VITE_APP_STAGE: "prod",
          VITE_API_ENDPOINT: "https://api.journalm8.com",
          VITE_COGNITO_DOMAIN: "https://auth.journalm8.com",
          VITE_COGNITO_CLIENT_ID: "client-prod-123",
          VITE_COGNITO_REDIRECT_URI: "https://app.journalm8.com/callback",
          VITE_COGNITO_LOGOUT_URI: "https://app.journalm8.com",
        }),
        { mode: "production", hasLegacyEnvLocal: true }
      ),
    /Migrate values to \.env\.development\.local/
  );
});

test("opaque execute-api host cannot bypass mode-stage mapping", () => {
  assert.throws(
    () =>
      validateFrontendEnv(
        baseEnv({
          VITE_APP_STAGE: "dev",
          VITE_API_ENDPOINT: "https://a1b2c3d4e5.execute-api.us-east-1.amazonaws.com",
          VITE_COGNITO_DOMAIN: "https://staging-auth.example.com",
          VITE_COGNITO_CLIENT_ID: "client-staging-123",
          VITE_COGNITO_REDIRECT_URI: "https://staging.example.com/callback",
          VITE_COGNITO_LOGOUT_URI: "https://staging.example.com",
        }),
        { mode: "staging" }
      ),
    /VITE_APP_STAGE must be 'staging' for mode 'staging'/
  );
});

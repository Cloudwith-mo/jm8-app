import test from "node:test";
import assert from "node:assert/strict";
import fs from "node:fs";
import os from "node:os";
import path from "node:path";

import {
  getApprovedModeLocalFiles,
  isApprovedModeSpecificEnvSource,
  validateFrontendEnv,
} from "./frontend_env_contract.mjs";
import { validateProductionBuild } from "./validate_production_build.mjs";

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
  assert.deepEqual(getApprovedModeLocalFiles("production"), [".env.production.local"]);
  assert.equal(isApprovedModeSpecificEnvSource(".env.production", "production"), false);
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
        {
          mode: "production",
          hasLegacyEnvLocal: true,
          hasProductionLocal: true,
        }
      ),
    /Migrate values to \.env\.development\.local/
  );
});

test("production accepts exact CloudFront-hosted configuration", () => {
  assert.deepEqual(
    validateFrontendEnv(
      baseEnv({
        VITE_APP_STAGE: "prod",
        VITE_API_ENDPOINT: "https://prod123abc.execute-api.us-east-1.amazonaws.com",
        VITE_COGNITO_DOMAIN: "https://journalm8-prod.auth.us-east-1.amazoncognito.com",
        VITE_COGNITO_CLIENT_ID: "client-prod-123",
        VITE_COGNITO_REDIRECT_URI: "https://prod123abc.cloudfront.net/callback",
        VITE_COGNITO_LOGOUT_URI: "https://prod123abc.cloudfront.net",
      }),
      { mode: "production", hasProductionLocal: true }
    ),
    { mode: "production", stage: "prod" }
  );
});

test("production requires only .env.production.local", () => {
  const environment = baseEnv({
    VITE_APP_STAGE: "prod",
    VITE_API_ENDPOINT: "https://prod123abc.execute-api.us-east-1.amazonaws.com",
    VITE_COGNITO_DOMAIN: "https://journalm8-prod.auth.us-east-1.amazoncognito.com",
    VITE_COGNITO_CLIENT_ID: "client-prod-123",
    VITE_COGNITO_REDIRECT_URI: "https://prod123abc.cloudfront.net/callback",
    VITE_COGNITO_LOGOUT_URI: "https://prod123abc.cloudfront.net",
  });
  assert.throws(
    () => validateFrontendEnv(environment, { mode: "production" }),
    /requires \.env\.production\.local/
  );
  assert.throws(
    () => validateFrontendEnv(environment, {
      mode: "production",
      hasProductionLocal: true,
      hasProductionEnv: true,
    }),
    /must not use \.env\.production/
  );
  assert.throws(
    () => validateFrontendEnv(environment, {
      mode: "production",
      hasProductionLocal: true,
      hasGenericEnv: true,
    }),
    /must not use generic \.env/
  );
});

test("production rejects cross-stage resources and plaintext secrets", () => {
  const production = {
    VITE_APP_STAGE: "prod",
    VITE_API_ENDPOINT: "https://prod123abc.execute-api.us-east-1.amazonaws.com",
    VITE_COGNITO_DOMAIN: "https://journalm8-prod.auth.us-east-1.amazoncognito.com",
    VITE_COGNITO_CLIENT_ID: "client-prod-123",
    VITE_COGNITO_REDIRECT_URI: "https://prod123abc.cloudfront.net/callback",
    VITE_COGNITO_LOGOUT_URI: "https://prod123abc.cloudfront.net",
  };
  assert.throws(
    () => validateFrontendEnv(
      { ...production, VITE_API_ENDPOINT: "https://journalm8-staging-api.example.com" },
      { mode: "production", hasProductionLocal: true }
    ),
    /staging\/dev identifiers/
  );
  let secretError = "";
  try {
    validateFrontendEnv(
      { ...production, VITE_STRIPE_SECRET_KEY: "credential-must-not-print" },
      { mode: "production", hasProductionLocal: true }
    );
  } catch (error) {
    secretError = String(error?.message || "");
  }
  assert.match(secretError, /forbidden variable VITE_STRIPE_SECRET_KEY/);
  assert.equal(secretError.includes("credential-must-not-print"), false);
});

test("production build validator rejects maps, cross-stage URLs, and unhashed assets", () => {
  const root = fs.mkdtempSync(path.join(os.tmpdir(), "jm8-prod-build-"));
  try {
    fs.mkdirSync(path.join(root, "assets"));
    fs.writeFileSync(path.join(root, "index.html"), "<html></html>");
    fs.writeFileSync(path.join(root, "assets", "index-12345678.js"), "console.log('prod')");
    assert.doesNotThrow(() => validateProductionBuild(root));

    fs.writeFileSync(path.join(root, "assets", "index-12345678.js.map"), "{}");
    assert.throws(() => validateProductionBuild(root), /source map is forbidden/);
    fs.rmSync(path.join(root, "assets", "index-12345678.js.map"));

    fs.writeFileSync(path.join(root, "assets", "plain.js"), "console.log('prod')");
    assert.throws(() => validateProductionBuild(root), /not content-hashed/);
    fs.rmSync(path.join(root, "assets", "plain.js"));

    fs.writeFileSync(
      path.join(root, "index.html"),
      "https://journalm8-staging-api.example.com"
    );
    assert.throws(() => validateProductionBuild(root), /cross-stage resource/);
  } finally {
    fs.rmSync(root, { recursive: true, force: true });
  }
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

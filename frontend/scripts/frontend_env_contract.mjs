const REQUIRED_VARIABLES = [
  "VITE_APP_STAGE",
  "VITE_API_ENDPOINT",
  "VITE_COGNITO_DOMAIN",
  "VITE_COGNITO_CLIENT_ID",
  "VITE_COGNITO_REDIRECT_URI",
  "VITE_COGNITO_LOGOUT_URI",
];

const MODE_TO_STAGE = {
  development: "dev",
  staging: "staging",
  production: "prod",
};

const DEV_IDENTIFIERS = ["localhost", "127.0.0.1", "-dev", "dev."];
const STAGING_IDENTIFIERS = ["localhost", "127.0.0.1", "-staging", "staging."];

export function getApprovedModeLocalFiles(mode) {
  if (mode === "production") {
    return [".env.production.local"];
  }
  return [`.env.${mode}.local`, `.env.${mode}`];
}

export function isApprovedModeSpecificEnvSource(source, mode) {
  if (!source) return false;
  return getApprovedModeLocalFiles(mode).includes(source);
}

function parseUrl(name, value) {
  try {
    return new URL(value);
  } catch {
    throw new Error(`${name} must be a valid absolute URL`);
  }
}

function hasAnyIdentifier(value, identifiers) {
  const lower = value.toLowerCase();
  return identifiers.some((identifier) => lower.includes(identifier));
}

function validateStageSpecificIdentifiers(stage, env) {
  const fields = [
    env.VITE_API_ENDPOINT,
    env.VITE_COGNITO_DOMAIN,
    env.VITE_COGNITO_CLIENT_ID,
    env.VITE_COGNITO_REDIRECT_URI,
    env.VITE_COGNITO_LOGOUT_URI,
  ].filter(Boolean);

  if (stage === "staging") {
    for (const field of fields) {
      if (hasAnyIdentifier(field, DEV_IDENTIFIERS)) {
        throw new Error("Staging frontend config must not contain dev identifiers");
      }
    }
  }

  if (stage === "prod") {
    for (const field of fields) {
      if (hasAnyIdentifier(field, STAGING_IDENTIFIERS) || hasAnyIdentifier(field, DEV_IDENTIFIERS)) {
        throw new Error("Production frontend config must not contain staging/dev identifiers");
      }
    }
  }
}

export function validateFrontendEnv(env, options = {}) {
  const mode = options.mode || "development";
  const hasLegacyEnvLocal = options.hasLegacyEnvLocal === true;
  const expectedStage = MODE_TO_STAGE[mode];

  if (!expectedStage) {
    throw new Error(`Unsupported frontend mode: ${mode}`);
  }

  if (hasLegacyEnvLocal && (mode === "staging" || mode === "production")) {
    throw new Error(
      "Legacy .env.local is not allowed for staging/production builds. "
      + "Migrate values to .env.development.local."
    );
  }

  if (mode === "production") {
    if (options.hasProductionLocal !== true) {
      throw new Error(
        "Production frontend configuration requires .env.production.local"
      );
    }
    if (options.hasProductionEnv === true) {
      throw new Error(
        "Production frontend configuration must not use .env.production"
      );
    }
    if (options.hasGenericEnv === true) {
      throw new Error(
        "Production frontend configuration must not use generic .env"
      );
    }
    const secretVariable = Object.keys(env).find(
      (name) => name.startsWith("VITE_")
        && /(SECRET|PASSWORD|PRIVATE_KEY|ACCESS_KEY)/i.test(name)
    );
    if (secretVariable) {
      throw new Error(
        `Production frontend configuration contains forbidden variable ${secretVariable}`
      );
    }
    const secretValueVariable = Object.keys(env).find((name) => {
      if (!name.startsWith("VITE_")) return false;
      const value = String(env[name] || "");
      return /(?:sk_(?:live|test)_|whsec_|AKIA[0-9A-Z]{16})/.test(value);
    });
    if (secretValueVariable) {
      throw new Error(
        `Production frontend configuration contains a secret-like value in ${secretValueVariable}`
      );
    }
  }

  const missing = REQUIRED_VARIABLES.filter((name) => {
    const value = env[name];
    return typeof value !== "string" || value.trim() === "";
  });

  if (missing.length > 0) {
    throw new Error(`Missing required frontend env variables: ${missing.join(", ")}`);
  }

  const stage = String(env.VITE_APP_STAGE).trim();
  if (stage !== expectedStage) {
    throw new Error(`VITE_APP_STAGE must be '${expectedStage}' for mode '${mode}'`);
  }

  const endpoint = parseUrl("VITE_API_ENDPOINT", env.VITE_API_ENDPOINT);
  const cognitoDomain = parseUrl("VITE_COGNITO_DOMAIN", env.VITE_COGNITO_DOMAIN);
  const redirectUrl = parseUrl("VITE_COGNITO_REDIRECT_URI", env.VITE_COGNITO_REDIRECT_URI);
  const logoutUrl = parseUrl("VITE_COGNITO_LOGOUT_URI", env.VITE_COGNITO_LOGOUT_URI);

  if (stage === "staging" || stage === "prod") {
    if (endpoint.protocol !== "https:") {
      throw new Error("VITE_API_ENDPOINT must use https for staging/prod");
    }
    if (cognitoDomain.protocol !== "https:") {
      throw new Error("VITE_COGNITO_DOMAIN must use https for staging/prod");
    }

    for (const [name, value] of [
      ["VITE_API_ENDPOINT", endpoint],
      ["VITE_COGNITO_DOMAIN", cognitoDomain],
      ["VITE_COGNITO_REDIRECT_URI", redirectUrl],
      ["VITE_COGNITO_LOGOUT_URI", logoutUrl],
    ]) {
      const host = value.hostname.toLowerCase();
      if (host === "localhost" || host === "127.0.0.1") {
        throw new Error(`${name} must not use localhost for staging/prod`);
      }
    }
  }

  validateStageSpecificIdentifiers(stage, env);

  return {
    mode,
    stage,
  };
}

export function assertFrontendEnv(env, options = {}) {
  validateFrontendEnv(env, options);
}

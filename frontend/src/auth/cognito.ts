const COGNITO_ENABLED = import.meta.env.VITE_COGNITO_ENABLED === "true";
const COGNITO_DOMAIN = import.meta.env.VITE_COGNITO_DOMAIN as string | undefined;
const COGNITO_CLIENT_ID = import.meta.env.VITE_COGNITO_CLIENT_ID as string | undefined;
const COGNITO_REDIRECT_URI = import.meta.env.VITE_COGNITO_REDIRECT_URI as string | undefined;
const COGNITO_LOGOUT_URI = import.meta.env.VITE_COGNITO_LOGOUT_URI as string | undefined;

const ACCESS_TOKEN_KEY = "jm8_access_token";
const ID_TOKEN_KEY = "jm8_id_token";
const REFRESH_TOKEN_KEY = "jm8_refresh_token";
const TOKEN_EXPIRES_AT_KEY = "jm8_token_expires_at";
const PKCE_VERIFIER_KEY = "jm8_pkce_verifier";

export type AuthUser = {
  sub?: string;
  email?: string;
  name?: string;
};

type CognitoTokenResponse = {
  access_token: string;
  id_token: string;
  refresh_token?: string;
  expires_in: number;
  token_type: string;
};

let callbackExchangePromise:
  Promise<AuthUser | null> | null = null;

function getRequiredConfig() {
  if (!COGNITO_ENABLED) {
    throw new Error("Cognito is not enabled.");
  }

  if (!COGNITO_DOMAIN || !COGNITO_CLIENT_ID || !COGNITO_REDIRECT_URI || !COGNITO_LOGOUT_URI) {
    throw new Error("Missing Cognito frontend environment variables.");
  }

  return {
    domain: COGNITO_DOMAIN,
    clientId: COGNITO_CLIENT_ID,
    redirectUri: COGNITO_REDIRECT_URI,
    logoutUri: COGNITO_LOGOUT_URI,
  };
}

function base64UrlEncode(buffer: ArrayBuffer) {
  const bytes = new Uint8Array(buffer);
  let binary = "";

  for (const byte of bytes) {
    binary += String.fromCharCode(byte);
  }

  return btoa(binary)
    .replace(/\+/g, "-")
    .replace(/\//g, "_")
    .replace(/=+$/, "");
}

function randomString(length = 96) {
  const values = new Uint8Array(length);
  crypto.getRandomValues(values);

  return Array.from(values)
    .map((value) => ("0" + value.toString(16)).slice(-2))
    .join("");
}

async function sha256(value: string) {
  const encoder = new TextEncoder();
  return crypto.subtle.digest("SHA-256", encoder.encode(value));
}

function decodeJwt(token: string): Record<string, unknown> {
  const [, payload] = token.split(".");

  if (!payload) return {};

  const normalized = payload.replace(/-/g, "+").replace(/_/g, "/");
  const padded = normalized.padEnd(normalized.length + ((4 - (normalized.length % 4)) % 4), "=");

  try {
    return JSON.parse(atob(padded));
  } catch {
    return {};
  }
}

export function getAccessToken() {
  const token = localStorage.getItem(ACCESS_TOKEN_KEY);
  const expiresAt = Number(localStorage.getItem(TOKEN_EXPIRES_AT_KEY) || "0");

  if (!token || Date.now() > expiresAt) {
    return null;
  }

  return token;
}

export function getIdToken() {
  return localStorage.getItem(ID_TOKEN_KEY);
}

export function getCurrentUser(): AuthUser | null {
  const idToken = getIdToken();

  if (!idToken || !getAccessToken()) return null;

  const claims = decodeJwt(idToken);

  return {
    sub: typeof claims.sub === "string" ? claims.sub : undefined,
    email: typeof claims.email === "string" ? claims.email : undefined,
    name: typeof claims.name === "string" ? claims.name : undefined,
  };
}

export function isAuthenticated() {
  return Boolean(getAccessToken());
}

export async function loginWithCognito() {
  const { domain, clientId, redirectUri } = getRequiredConfig();

  const codeVerifier = randomString();
  const codeChallenge = base64UrlEncode(await sha256(codeVerifier));

  localStorage.setItem(PKCE_VERIFIER_KEY, codeVerifier);

  const params = new URLSearchParams({
    client_id: clientId,
    response_type: "code",
    scope: "openid email profile",
    redirect_uri: redirectUri,
    code_challenge_method: "S256",
    code_challenge: codeChallenge,
  });

  window.location.assign(`${domain}/oauth2/authorize?${params.toString()}`);
}

async function exchangeCognitoCallback() {
  const url = new URL(window.location.href);
  const code = url.searchParams.get("code");
  const error = url.searchParams.get("error");

  if (error) {
    const errorDescription =
      url.searchParams.get(
        "error_description"
      );

    clearAuthTokens();
    url.search = "";

    window.history.replaceState(
      {},
      document.title,
      url.toString()
    );

    throw new Error(
      errorDescription || error
    );
  }

  if (!code) return null;

  const { domain, clientId, redirectUri } = getRequiredConfig();
  const codeVerifier = localStorage.getItem(PKCE_VERIFIER_KEY);

  if (!codeVerifier) {
    throw new Error("Missing PKCE verifier. Please sign in again.");
  }

  const body = new URLSearchParams({
    grant_type: "authorization_code",
    client_id: clientId,
    code,
    redirect_uri: redirectUri,
    code_verifier: codeVerifier,
  });

  const response = await fetch(`${domain}/oauth2/token`, {
    method: "POST",
    headers: {
      "content-type": "application/x-www-form-urlencoded",
    },
    body,
  });

  if (!response.ok) {
    localStorage.removeItem(
      PKCE_VERIFIER_KEY
    );

    url.search = "";

    window.history.replaceState(
      {},
      document.title,
      url.toString()
    );

    throw new Error(
      `Token exchange failed: ${response.status}`
    );
  }

  const tokens = (await response.json()) as CognitoTokenResponse;
  const expiresAt = Date.now() + tokens.expires_in * 1000 - 30_000;

  localStorage.setItem(ACCESS_TOKEN_KEY, tokens.access_token);
  localStorage.setItem(ID_TOKEN_KEY, tokens.id_token);
  localStorage.setItem(TOKEN_EXPIRES_AT_KEY, String(expiresAt));

  if (tokens.refresh_token) {
    localStorage.setItem(REFRESH_TOKEN_KEY, tokens.refresh_token);
  }

  localStorage.removeItem(PKCE_VERIFIER_KEY);

  url.search = "";
  window.history.replaceState({}, document.title, url.toString());

  return getCurrentUser();
}

export function handleCognitoCallback():
  Promise<AuthUser | null> {
  if (!callbackExchangePromise) {
    callbackExchangePromise =
      exchangeCognitoCallback().finally(
        () => {
          callbackExchangePromise = null;
        }
      );
  }

  return callbackExchangePromise;
}


export function clearAuthTokens() {
  localStorage.removeItem(ACCESS_TOKEN_KEY);
  localStorage.removeItem(ID_TOKEN_KEY);
  localStorage.removeItem(REFRESH_TOKEN_KEY);
  localStorage.removeItem(TOKEN_EXPIRES_AT_KEY);
  localStorage.removeItem(PKCE_VERIFIER_KEY);
}

export function logoutFromCognito() {
  const { domain, clientId, logoutUri } = getRequiredConfig();

  clearAuthTokens();

  const params = new URLSearchParams({
    client_id: clientId,
    logout_uri: logoutUri,
  });

  window.location.assign(`${domain}/logout?${params.toString()}`);
}

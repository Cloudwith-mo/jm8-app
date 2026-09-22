import { serviceFetch } from "../platform/http";
import { authStorage, isNativeIos, nativeCallback, openNativeAuthorization, persistNativeSession, restoreNativeSession, clearNativeSession } from "./nativeSession";
import { frontendEnv } from "../config/env";

const COGNITO_ENABLED = frontendEnv.cognitoEnabled;
const COGNITO_DOMAIN = frontendEnv.cognitoDomain;
const COGNITO_CLIENT_ID = frontendEnv.cognitoClientId;
const COGNITO_REDIRECT_URI = frontendEnv.cognitoRedirectUri;
const COGNITO_LOGOUT_URI = frontendEnv.cognitoLogoutUri;

const ACCESS_TOKEN_KEY = "jm8_access_token";
const ID_TOKEN_KEY = "jm8_id_token";
const REFRESH_TOKEN_KEY = "jm8_refresh_token";
const TOKEN_EXPIRES_AT_KEY = "jm8_token_expires_at";
const PKCE_VERIFIER_KEY = "jm8_pkce_verifier";
const OAUTH_STATE_KEY = "jm8_oauth_state";
const ACCOUNT_DELETION_RETURN_KEY = "jm8_auth_return_intent";
const ACCOUNT_DELETION_ACKNOWLEDGEMENT_KEY = "jm8_deletion_acknowledgement";
const ACCOUNT_DELETION_RETURN_VALUE = "account-deletion";
const ACCOUNT_DELETION_ACKNOWLEDGEMENT_VALUE = "processed";

export const AUTH_SESSION_EXPIRED_EVENT = "jm8:auth-session-expired";

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

  if (isNativeIos && (frontendEnv.appStage !== "dev"
    || COGNITO_DOMAIN !== "https://journalm8-dev-114743615542.auth.us-east-1.amazoncognito.com"
    || COGNITO_CLIENT_ID !== "4t37mcfdkg5gdvl7ev8vt91ojg"
    || frontendEnv.apiEndpoint !== "https://u06tdrfsua.execute-api.us-east-1.amazonaws.com")) {
    throw new Error("The iOS prototype requires the approved development environment.");
  }

  return {
    domain: COGNITO_DOMAIN,
    clientId: COGNITO_CLIENT_ID,
    redirectUri: isNativeIos ? nativeCallback : COGNITO_REDIRECT_URI,
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

function parseTokenResponse(value: unknown): CognitoTokenResponse {
  if (!value || typeof value !== "object" || Array.isArray(value)) {
    throw new Error("Cognito returned an invalid token response.");
  }

  const response = value as Record<string, unknown>;
  const accessToken = response.access_token;
  const idToken = response.id_token;
  const expiresIn = response.expires_in;
  const tokenType = response.token_type;
  const refreshToken = response.refresh_token;

  if (
    typeof accessToken !== "string" || !accessToken.trim()
    || typeof idToken !== "string" || !idToken.trim()
    || typeof expiresIn !== "number" || !Number.isSafeInteger(expiresIn) || expiresIn <= 0
    || tokenType !== "Bearer"
    || (refreshToken !== undefined && (typeof refreshToken !== "string" || !refreshToken.trim()))
  ) {
    throw new Error("Cognito returned an invalid token response.");
  }

  const claims = decodeJwt(idToken);
  if (typeof claims.sub !== "string" || !claims.sub.trim()) {
    throw new Error("Cognito returned an invalid identity token.");
  }

  return {
    access_token: accessToken,
    id_token: idToken,
    expires_in: expiresIn,
    token_type: tokenType,
    ...(typeof refreshToken === "string" ? { refresh_token: refreshToken } : {}),
  };
}

function removeCallbackParameters(url: URL) {
  if (isNativeIos) return;
  url.search = "";
  url.hash = "";
  window.history.replaceState({}, document.title, url.toString());
}

export function getAccessToken() {
  const token = authStorage.getItem(ACCESS_TOKEN_KEY);
  const expiresAt = Number(authStorage.getItem(TOKEN_EXPIRES_AT_KEY) || "0");

  if (!token || Date.now() > expiresAt) {
    return null;
  }

  return token;
}

export function getAuthSessionExpiresAt(): number | null {
  const value = Number(authStorage.getItem(TOKEN_EXPIRES_AT_KEY));
  return Number.isFinite(value) && value > 0 ? value : null;
}

export function getIdToken() {
  return authStorage.getItem(ID_TOKEN_KEY);
}

export function getCurrentUser(): AuthUser | null {
  const idToken = getIdToken();

  if (!idToken || !getAccessToken()) return null;

  const claims = decodeJwt(idToken);

  if (typeof claims.sub !== "string" || !claims.sub.trim()) return null;

  return {
    sub: claims.sub,
    email: typeof claims.email === "string" ? claims.email : undefined,
    name: typeof claims.name === "string" ? claims.name : undefined,
  };
}

export function isAuthenticated() {
  return Boolean(getAccessToken());
}

async function beginCognitoAuthorization(path: "/oauth2/authorize" | "/signup") {
  const { domain, clientId, redirectUri } = getRequiredConfig();

  if (isNativeIos) await clearNativeSession();
  const codeVerifier = randomString();
  const codeChallenge = base64UrlEncode(await sha256(codeVerifier));
  const state = randomString(32);

  authStorage.setItem(PKCE_VERIFIER_KEY, codeVerifier);
  authStorage.setItem(OAUTH_STATE_KEY, state);

  const params = new URLSearchParams({
    client_id: clientId,
    response_type: "code",
    scope: "openid email profile",
    redirect_uri: redirectUri,
    code_challenge_method: "S256",
    code_challenge: codeChallenge,
    state,
  });

  const authorizationUrl = `${domain}${path}?${params.toString()}`;
  if (isNativeIos) {
    try {
      const callback = await openNativeAuthorization(authorizationUrl);
      await exchangeCognitoCallback(callback);
      window.location.reload();
    } catch (error) {
      clearAuthTokens();
      throw error;
    }
    return;
  }
  window.location.assign(authorizationUrl);
}

export async function loginWithCognito() {
  await beginCognitoAuthorization("/oauth2/authorize");
}

export async function signupWithCognito() {
  await beginCognitoAuthorization("/signup");
}

async function exchangeCognitoCallback(url = new URL(window.location.href)) {
  const code = url.searchParams.get("code");
  const error = url.searchParams.get("error");
  const callbackState = url.searchParams.get("state");
  const hasCallbackParameters = ["code", "error", "error_description", "state"]
    .some((name) => url.searchParams.has(name));
  const hasAmbiguousCallbackParameters = ["code", "error", "error_description", "state"]
    .some((name) => url.searchParams.getAll(name).length > 1);

  if (hasAmbiguousCallbackParameters) {
    clearAuthTokens();
    removeCallbackParameters(url);
    throw new Error("Cognito returned an ambiguous authorization response.");
  }

  if (error) {
    clearAuthTokens();
    removeCallbackParameters(url);
    throw new Error("Cognito authorization was not completed.");
  }

  if (!code) {
    if (hasCallbackParameters) {
      clearAuthTokens();
      removeCallbackParameters(url);
      throw new Error("Cognito returned an incomplete authorization response.");
    }
    return null;
  }

  const { domain, clientId, redirectUri } = getRequiredConfig();
  const codeVerifier = authStorage.getItem(PKCE_VERIFIER_KEY);
  const expectedState = authStorage.getItem(OAUTH_STATE_KEY);

  if (!codeVerifier || !expectedState || callbackState !== expectedState) {
    clearAuthTokens();
    removeCallbackParameters(url);
    throw new Error("Cognito authorization validation failed. Please sign in again.");
  }

  const body = new URLSearchParams({
    grant_type: "authorization_code",
    client_id: clientId,
    code,
    redirect_uri: redirectUri,
    code_verifier: codeVerifier,
  });

  try {
    const response = await serviceFetch(`${domain}/oauth2/token`, {
      method: "POST",
      headers: {
        "content-type": "application/x-www-form-urlencoded",
      },
      body,
    });

    if (!response.ok) {
      throw new Error(`Token exchange failed: ${response.status}`);
    }

    const tokens = parseTokenResponse(await response.json());
    const expiresAt = Date.now() + tokens.expires_in * 1000 - 30_000;

    authStorage.setItem(ACCESS_TOKEN_KEY, tokens.access_token);
    authStorage.setItem(ID_TOKEN_KEY, tokens.id_token);
    authStorage.setItem(TOKEN_EXPIRES_AT_KEY, String(expiresAt));

    if (tokens.refresh_token) {
      authStorage.setItem(REFRESH_TOKEN_KEY, tokens.refresh_token);
    }

    await persistNativeSession();
    return getCurrentUser();
  } catch (exchangeError) {
    clearAuthTokens();
    throw exchangeError;
  } finally {
    authStorage.removeItem(PKCE_VERIFIER_KEY);
    authStorage.removeItem(OAUTH_STATE_KEY);
    removeCallbackParameters(url);
  }
}

export function handleCognitoCallback():
  Promise<AuthUser | null> {
  if (!callbackExchangePromise) {
    callbackExchangePromise =
      (isNativeIos
        ? restoreNativeSession().then(() => null)
        : exchangeCognitoCallback()).finally(
        () => {
          callbackExchangePromise = null;
        }
      );
  }

  return callbackExchangePromise;
}


export function clearAuthTokens() {
  // Protected UI is cleared synchronously; the tombstone blocks failed restores.
  void clearNativeSession().catch(() => {
    window.dispatchEvent(new Event("jm8:secure-session-clear-failed"));
  });
  authStorage.removeItem(ACCESS_TOKEN_KEY);
  authStorage.removeItem(ID_TOKEN_KEY);
  authStorage.removeItem(REFRESH_TOKEN_KEY);
  authStorage.removeItem(TOKEN_EXPIRES_AT_KEY);
  authStorage.removeItem(PKCE_VERIFIER_KEY);
  authStorage.removeItem(OAUTH_STATE_KEY);
}

export function rememberAccountDeletionReturnIntent() {
  sessionStorage.setItem(
    ACCOUNT_DELETION_RETURN_KEY,
    ACCOUNT_DELETION_RETURN_VALUE,
  );
}

export function consumeAccountDeletionReturnIntent(): boolean {
  const matches = sessionStorage.getItem(ACCOUNT_DELETION_RETURN_KEY)
    === ACCOUNT_DELETION_RETURN_VALUE;
  sessionStorage.removeItem(ACCOUNT_DELETION_RETURN_KEY);
  return matches;
}

export function clearAccountDeletionReturnIntent() {
  sessionStorage.removeItem(ACCOUNT_DELETION_RETURN_KEY);
}

export function rememberAccountDeletionAcknowledgement() {
  sessionStorage.setItem(
    ACCOUNT_DELETION_ACKNOWLEDGEMENT_KEY,
    ACCOUNT_DELETION_ACKNOWLEDGEMENT_VALUE,
  );
}

export function consumeAccountDeletionAcknowledgement(): boolean {
  const matches = sessionStorage.getItem(ACCOUNT_DELETION_ACKNOWLEDGEMENT_KEY)
    === ACCOUNT_DELETION_ACKNOWLEDGEMENT_VALUE;
  sessionStorage.removeItem(ACCOUNT_DELETION_ACKNOWLEDGEMENT_KEY);
  return matches;
}

export function expireAuthSession() {
  clearAuthTokens();
  window.dispatchEvent(new Event(AUTH_SESSION_EXPIRED_EVENT));
}

export function logoutFromCognito() {
  clearAuthTokens();

  if (isNativeIos) {
    // Authentication uses an ephemeral browser session; no shared SSO cookie remains.
    void clearNativeSession().then(() => window.location.replace("/")).catch(() => {
      window.dispatchEvent(new Event("jm8:secure-session-clear-failed"));
    });
    return;
  }
  const { domain, clientId, logoutUri } = getRequiredConfig();

  const params = new URLSearchParams({
    client_id: clientId,
    logout_uri: logoutUri,
  });

  window.location.assign(`${domain}/logout?${params.toString()}`);
}

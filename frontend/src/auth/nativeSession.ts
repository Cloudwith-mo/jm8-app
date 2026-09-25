import { Capacitor, registerPlugin } from "@capacitor/core";

export const isNativeIos = Capacitor.getPlatform() === "ios";
export const nativeCallback = "com.cloudwithmo.journalm8.dev://auth/callback";
const signedOutKey = "jm8_native_signed_out";
const persistedKeys = ["jm8_access_token", "jm8_id_token", "jm8_refresh_token", "jm8_token_expires_at"];
const memory = new Map<string, string>();
let sessionEpoch = 0;
const NativeAuth = registerPlugin<{
  authenticate(options: { url: string; callback: string }): Promise<{ url: string }>;
  getSession(): Promise<{ value?: string }>;
  setSession(options: { value: string }): Promise<void>;
  clearSession(): Promise<void>;
}>("JM8NativeAuth");

// Tokens and the PKCE verifier stay out of WebView localStorage on iOS.
export const authStorage = {
  getItem(key: string): string | null {
    return isNativeIos ? memory.get(key) ?? null : localStorage.getItem(key);
  },
  setItem(key: string, value: string) {
    if (isNativeIos) memory.set(key, value); else localStorage.setItem(key, value);
  },
  removeItem(key: string) {
    if (isNativeIos) memory.delete(key); else localStorage.removeItem(key);
  },
};
let storageQueue: Promise<void> = Promise.resolve();
function enqueue(action: () => Promise<void>) {
  const result = storageQueue.then(action);
  storageQueue = result.catch(() => {});
  return result;
}

export function clearNativeSession() {
  if (!isNativeIos) return Promise.resolve();
  sessionEpoch += 1;
  memory.clear();
  // A non-secret tombstone prevents restoring a signed-out session if deletion fails.
  localStorage.setItem(signedOutKey, "true");
  return enqueue(() => NativeAuth.clearSession());
}

export async function persistNativeSession() {
  if (!isNativeIos) return;
  const epoch = sessionEpoch;
  const value = JSON.stringify(Object.fromEntries(
    persistedKeys.flatMap(key => memory.has(key) ? [[key, memory.get(key)!]] : []),
  ));
  await enqueue(() => NativeAuth.setSession({ value }));
  if (epoch !== sessionEpoch) throw new Error("Sign-in was interrupted. Please try again.");
  localStorage.removeItem(signedOutKey);
}

export async function restoreNativeSession() {
  if (!isNativeIos) return;
  if (localStorage.getItem(signedOutKey)) {
    await clearNativeSession();
    return;
  }
  const epoch = sessionEpoch;
  const { value } = await NativeAuth.getSession();
  if (!value || epoch !== sessionEpoch || localStorage.getItem(signedOutKey)) return;
  try {
    const data: unknown = JSON.parse(value);
    if (!data || typeof data !== "object" || Array.isArray(data)) throw new Error();
    const record = data as Record<string, unknown>;
    if (Object.keys(record).some(key => !persistedKeys.includes(key))
      || Object.values(record).some(item => typeof item !== "string")
      || !record.jm8_access_token || !record.jm8_id_token
      || !Number.isFinite(Number(record.jm8_token_expires_at))
      || Number(record.jm8_token_expires_at) <= Date.now()) throw new Error();
    for (const key of persistedKeys) {
      if (typeof record[key] === "string") memory.set(key, record[key]);
    }
  } catch {
    await clearNativeSession();
  }
}

export async function openNativeAuthorization(url: string) {
  const result = await NativeAuth.authenticate({ url, callback: nativeCallback });
  const callback = new URL(result.url);
  if (`${callback.protocol}//${callback.host}${callback.pathname}` !== nativeCallback || callback.hash) {
    throw new Error("Unexpected sign-in callback.");
  }
  return callback;
}

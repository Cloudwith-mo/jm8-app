import { Capacitor, CapacitorHttp } from "@capacitor/core";

const nativeOrigins = new Set([
  "https://u06tdrfsua.execute-api.us-east-1.amazonaws.com",
  "https://journalm8-dev-114743615542.auth.us-east-1.amazoncognito.com",
]);

/** Native transport for dev API JSON and Cognito token requests only. */
export async function serviceFetch(url: string, options: RequestInit = {}): Promise<Response> {
  if (Capacitor.getPlatform() !== "ios") return fetch(url, options);
  const target = new URL(url);
  if (!nativeOrigins.has(target.origin) || target.username || target.password
    || (target.hostname.endsWith("amazoncognito.com") && target.pathname !== "/oauth2/token")) {
    throw new Error("Unsupported native service URL.");
  }
  const signal = options.signal;
  if (signal?.aborted) throw signal.reason ?? new DOMException("Aborted", "AbortError");
  const body = options.body;
  if (body != null && typeof body !== "string" && !(body instanceof URLSearchParams)) {
    throw new Error("Native service requests require a text body.");
  }
  const headers = Object.fromEntries(new Headers(options.headers).entries());
  const request = CapacitorHttp.request({
    url,
    method: options.method ?? "GET",
    headers,
    ...(body != null ? { data: body.toString() } : {}),
    responseType: "text",
    disableRedirects: true,
    shouldEncodeUrlParams: false,
    connectTimeout: 15000,
    readTimeout: 60000,
  });
  // The native request cannot be canceled by AbortSignal. Reject the caller
  // promptly and discard its eventual result, preserving UI cancellation rules.
  let onAbort: (() => void) | undefined;
  try {
    const result = await (signal ? Promise.race([
      request,
      new Promise<never>((_, reject) => {
        onAbort = () => reject(signal.reason ?? new DOMException("Aborted", "AbortError"));
        signal.addEventListener("abort", onAbort, { once: true });
        if (signal.aborted) onAbort();
      }),
    ]) : request);
    const payload = result.data == null ? "" : typeof result.data === "string"
      ? result.data : JSON.stringify(result.data);
    return new Response([204, 205, 304].includes(result.status) || options.method === "HEAD" ? null : payload, {
      status: result.status,
      headers: result.headers,
    });
  } finally {
    if (signal && onAbort) signal.removeEventListener("abort", onAbort);
  }
}

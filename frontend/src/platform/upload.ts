import { Capacitor, CapacitorHttp } from "@capacitor/core";

const nativeUploadOrigins = new Set([
  "https://journalm8-dev-raw-114743615542.s3.amazonaws.com",
  "https://journalm8-dev-raw-114743615542.s3.us-east-1.amazonaws.com",
]);
const maxNativeUploadBytes = 10 * 1024 * 1024;

export function uploadContentType(file: File): string {
  return file.type || "image/jpeg";
}

export function validateImageUpload(file: File): void {
  // Base64 crosses the native bridge in memory; bound that allocation.
  if (Capacitor.getPlatform() === "ios" && (file.size === 0 || file.size > maxNativeUploadBytes)) {
    throw new Error("Choose a non-empty journal image no larger than 10 MiB.");
  }
}

export async function uploadJournalImage(uploadUrl: string, file: File): Promise<void> {
  validateImageUpload(file);
  const contentType = uploadContentType(file);
  if (Capacitor.getPlatform() !== "ios") {
    const response = await fetch(uploadUrl, {
      method: "PUT", headers: { "content-type": contentType }, body: file,
    });
    if (!response.ok) throw new Error(`Image upload failed (HTTP ${response.status}). Please retry.`);
    return;
  }

  const target = new URL(uploadUrl);
  if (!nativeUploadOrigins.has(target.origin) || target.username || target.password
    || target.hash || !target.pathname.startsWith("/users/")) {
    throw new Error("Unsupported development image upload destination.");
  }
  const bytes = new Uint8Array(await file.arrayBuffer());
  const chunks: string[] = [];
  for (let offset = 0; offset < bytes.length; offset += 32768) {
    chunks.push(String.fromCharCode(...bytes.subarray(offset, offset + 32768)));
  }
  // Capacitor 8.5.2 decodes dataType:file base64 into raw Data on iOS.
  // Keep the original signed URL, with no URLSearchParams serialization.
  let status: number;
  try {
    const response = await CapacitorHttp.request({
      url: uploadUrl,
      method: "PUT",
      headers: { "Content-Type": contentType },
      data: btoa(chunks.join("")),
      dataType: "file",
      responseType: "text",
      disableRedirects: true,
      shouldEncodeUrlParams: false,
      connectTimeout: 120000,
      readTimeout: 120000,
    });
    status = response.status;
  } catch {
    // Do not surface native error payloads containing signed URLs or image data.
    throw new Error("Image transfer failed. Check your connection and retry Upload + OCR.");
  }
  if (status < 200 || status >= 300) {
    throw new Error(`Image upload failed (HTTP ${status}). Please retry Upload + OCR.`);
  }
}

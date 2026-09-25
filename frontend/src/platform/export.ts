import { Capacitor, registerPlugin } from "@capacitor/core";

const nativeExport = registerPlugin<{
  shareTranscript(options: { filename: string; text: string }): Promise<{ completed: boolean }>;
}>("JM8NativeExport");

export async function exportTranscriptFile(
  filename: string, text: string
): Promise<"shared" | "cancelled" | "download-started"> {
  if (Capacitor.getPlatform() === "ios") {
    const result = await nativeExport.shareTranscript({ filename, text });
    return result.completed ? "shared" : "cancelled";
  }
  const blob = new Blob([text], { type: "text/plain;charset=utf-8" });
  const url = window.URL.createObjectURL(blob);
  const anchor = document.createElement("a");
  anchor.href = url;
  anchor.download = filename;
  document.body.appendChild(anchor);
  try {
    anchor.click();
  } finally {
    anchor.remove();
    window.setTimeout(() => window.URL.revokeObjectURL(url), 1000);
  }
  return "download-started";
}

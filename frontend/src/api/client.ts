import { getAccessToken } from "../auth/cognito";
import type { JournalEntry, UploadResponse } from "../types/journal";

const API_ENDPOINT = import.meta.env.VITE_API_ENDPOINT;
const DEMO_USER_ID = import.meta.env.VITE_DEMO_USER_ID || "demo-user";

type ApiResponse<T> = T;

async function apiRequest<T>(
  path: string,
  options: RequestInit = {}
): Promise<ApiResponse<T>> {
  const response = await fetch(`${API_ENDPOINT}${path}`, {
    ...options,
    headers: {
      "content-type": "application/json",
      "x-user-id": DEMO_USER_ID,
      ...(getAccessToken() ? { Authorization: `Bearer ${getAccessToken()}` } : {}),
      ...(options.headers || {}),
    },
  });

  const data = await response.json();

  if (!response.ok) {
    throw new Error(data?.message || data?.error || "API request failed");
  }

  return data;
}

export async function createEntry(text: string): Promise<{ message: string; entry: JournalEntry }> {
  return apiRequest("/entries", {
    method: "POST",
    body: JSON.stringify({ text }),
  });
}

export async function listEntries(): Promise<{ count: number; entries: JournalEntry[] }> {
  return apiRequest("/entries");
}

export async function getEntry(entryId: string): Promise<{ entry: JournalEntry }> {
  return apiRequest(`/entries/${entryId}`);
}

export async function analyzeEntry(entryId: string): Promise<{ message: string; entry: JournalEntry }> {
  return apiRequest(`/entries/${entryId}/analyze`, {
    method: "POST",
  });
}

export async function createUploadUrl(
  fileName: string,
  contentType: string
): Promise<UploadResponse> {
  return apiRequest("/upload-url", {
    method: "POST",
    body: JSON.stringify({
      fileName,
      contentType,
    }),
  });
}

export async function uploadFileToS3(uploadUrl: string, file: File): Promise<void> {
  const response = await fetch(uploadUrl, {
    method: "PUT",
    headers: {
      "content-type": file.type,
    },
    body: file,
  });

  if (!response.ok) {
    throw new Error("S3 upload failed");
  }
}

export type OcrJobAcceptedResponse = {
  message: string;
  entry: JournalEntry;
  job: {
    entryId: string;
    jobStatus: "PENDING";
    executionArn: string;
    executionName: string;
    startedAt: string;
  };
};

export async function runOcr(
  entryId: string
): Promise<OcrJobAcceptedResponse> {
  return apiRequest(`/entries/${entryId}/ocr`, {
    method: "POST",
  });
}

function sleep(milliseconds: number): Promise<void> {
  return new Promise((resolve) => {
    window.setTimeout(resolve, milliseconds);
  });
}

export async function waitForOcrCompletion(
  entryId: string,
  maxPolls = 60,
  intervalMilliseconds = 1500
): Promise<{ entry: JournalEntry }> {
  for (let poll = 0; poll < maxPolls; poll += 1) {
    const result = await getEntry(entryId);
    const status = String(result.entry.status || "").toUpperCase();
    const ocrStatus = String(result.entry.ocrStatus || "").toUpperCase();

    if (
      status === "OCR_COMPLETED" ||
      ocrStatus === "COMPLETED"
    ) {
      return result;
    }

    if (
      status === "OCR_FAILED" ||
      ocrStatus === "FAILED"
    ) {
      throw new Error(
        "OCR processing failed. The job can be retried."
      );
    }

    await sleep(intervalMilliseconds);
  }

  throw new Error(
    "OCR is still processing. Refresh the archive shortly."
  );
}

export async function reviewEntry(
  entryId: string,
  cleanText: string
): Promise<{ message: string; entry: JournalEntry }> {
  return apiRequest(`/entries/${entryId}/review`, {
    method: "PUT",
    body: JSON.stringify({ cleanText }),
  });
}

export async function deleteEntry(entryId: string) {
  return apiRequest<{
    message: string;
    result: {
      entryId: string;
      deleted: boolean;
      deletedImage?: boolean;
    };
  }>(`/entries/${entryId}`, {
    method: "DELETE",
  });
}

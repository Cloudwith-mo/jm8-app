import { getAccessToken } from "../auth/cognito";
import type {
  AnalysisHistoryResponse,
  JournalEntry,
  UploadResponse,
} from "../types/journal";
import type {
  InsightsMoodsResponse,
  InsightsOverviewResponse,
  InsightsThemesResponse,
} from "../types/insights";
import type {
  InsightsReportResponse,
} from "../types/reports";
import type {
  AskJm8HistoryDeleteResponse,
  AskJm8HistoryDetailResponse,
  AskJm8HistoryListResponse,
  AskJm8Request,
  AskJm8Response,
} from "../types/askJm8";
import type {
  HistoricalReanalysisDryRunResponse,
  HistoricalReanalysisJobAcceptedResponse,
  HistoricalReanalysisJobListResponse,
  HistoricalReanalysisJobStatusFilter,
} from "../types/reanalysis";
import type {
  UsageResponse,
} from "../types/usage";

const API_ENDPOINT = import.meta.env.VITE_API_ENDPOINT;
const DEMO_USER_ID = import.meta.env.VITE_DEMO_USER_ID || "demo-user";

type ApiErrorPayload = Record<string, unknown>;

export class ApiRequestError extends Error {
  readonly status: number;
  readonly code?: string;
  readonly payload: ApiErrorPayload;

  constructor(
    status: number,
    message: string,
    code: string | undefined,
    payload: ApiErrorPayload
  ) {
    super(message);
    this.name = "ApiRequestError";
    this.status = status;
    this.code = code;
    this.payload = payload;
  }
}

async function parseApiResponse(
  response: Response
): Promise<Record<string, unknown>> {
  const text = await response.text();

  if (!text) return {};

  try {
    const parsed: unknown = JSON.parse(text);

    if (
      parsed &&
      typeof parsed === "object" &&
      !Array.isArray(parsed)
    ) {
      return parsed as Record<string, unknown>;
    }

    return {};
  } catch {
    return {
      message: text,
    };
  }
}

async function apiRequest<T>(
  path: string,
  options: RequestInit = {}
): Promise<T> {
  const accessToken = getAccessToken();

  const response = await fetch(`${API_ENDPOINT}${path}`, {
    ...options,
    headers: {
      "content-type": "application/json",
      "x-user-id": DEMO_USER_ID,
      ...(accessToken
        ? {
            Authorization: `Bearer ${accessToken}`,
          }
        : {}),
      ...(options.headers || {}),
    },
  });

  const data = await parseApiResponse(response);

  if (!response.ok) {
    const message =
      typeof data.message === "string"
        ? data.message
        : typeof data.error === "string"
          ? data.error
          : "API request failed";

    const code =
      typeof data.error === "string"
        ? data.error
        : undefined;

    throw new ApiRequestError(
      response.status,
      message,
      code,
      data
    );
  }

  return data as T;
}

export async function getUsage():
Promise<UsageResponse> {
  return apiRequest(
    "/usage"
  );
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

export async function getAnalysisHistory(
  entryId: string,
  limit = 20
): Promise<AnalysisHistoryResponse> {
  const query = new URLSearchParams({
    limit: String(limit),
  });

  return apiRequest(
    `/entries/${entryId}/analysis-history?${query.toString()}`
  );
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


export async function getInsightsOverview():
Promise<InsightsOverviewResponse> {
  return apiRequest(
    "/insights/overview"
  );
}


export async function getInsightsThemes():
Promise<InsightsThemesResponse> {
  return apiRequest(
    "/insights/themes"
  );
}


export async function getInsightsMoods():
Promise<InsightsMoodsResponse> {
  return apiRequest(
    "/insights/moods"
  );
}


function getReportPath(
  path: string,
  period?: string
): string {
  const normalizedPeriod =
    period?.trim();

  if (!normalizedPeriod) {
    return path;
  }

  const query = new URLSearchParams({
    period: normalizedPeriod,
  });

  return `${path}?${query.toString()}`;
}


export async function getWeeklyReport(
  period?: string
): Promise<InsightsReportResponse> {
  return apiRequest(
    getReportPath(
      "/reports/weekly",
      period
    )
  );
}


export async function getMonthlyReport(
  period?: string
): Promise<InsightsReportResponse> {
  return apiRequest(
    getReportPath(
      "/reports/monthly",
      period
    )
  );
}


export async function askJm8(
  request: AskJm8Request
): Promise<AskJm8Response> {
  const question =
    request.question.trim();

  const startDate =
    request.startDate?.trim();

  const endDate =
    request.endDate?.trim();

  const payload: AskJm8Request = {
    question,
    ...(startDate
      ? {
          startDate,
        }
      : {}),
    ...(endDate
      ? {
          endDate,
        }
      : {}),
  };

  return apiRequest<AskJm8Response>(
    "/insights/ask",
    {
      method: "POST",
      body: JSON.stringify(
        payload
      ),
    }
  );
}


export async function listAskJm8History({
  limit = 20,
  cursor,
}: {
  limit?: number;
  cursor?: string | null;
} = {}): Promise<AskJm8HistoryListResponse> {
  const query = new URLSearchParams({
    limit: String(limit),
  });

  const normalizedCursor =
    cursor?.trim();

  if (normalizedCursor) {
    query.set(
      "cursor",
      normalizedCursor
    );
  }

  return apiRequest(
    (
      "/insights/ask/history?"
      + query.toString()
    )
  );
}


export async function getAskJm8History(
  historyId: string
): Promise<AskJm8HistoryDetailResponse> {
  return apiRequest(
    (
      "/insights/ask/history/"
      + encodeURIComponent(historyId)
    )
  );
}


export async function deleteAskJm8History(
  historyId: string
): Promise<AskJm8HistoryDeleteResponse> {
  return apiRequest(
    (
      "/insights/ask/history/"
      + encodeURIComponent(historyId)
    ),
    {
      method: "DELETE",
    }
  );
}


export async function listHistoricalReanalysisJobs(
  status: HistoricalReanalysisJobStatusFilter = "ALL",
  limit = 50
): Promise<HistoricalReanalysisJobListResponse> {
  const query = new URLSearchParams({
    status,
    limit: String(limit),
  });

  return apiRequest(
    `/analysis/reanalysis/jobs?${query.toString()}`
  );
}

export async function getHistoricalReanalysisInventory():
Promise<HistoricalReanalysisDryRunResponse> {
  return apiRequest(
    "/analysis/reanalysis/dry-run"
  );
}

export async function startHistoricalReanalysisJob(
  pageSize = 10
): Promise<HistoricalReanalysisJobAcceptedResponse> {
  return apiRequest(
    "/analysis/reanalysis/jobs",
    {
      method: "POST",
      body: JSON.stringify({
        pageSize,
      }),
    }
  );
}

export async function retryHistoricalReanalysisJob(
  jobId: string,
  pageSize = 10
): Promise<HistoricalReanalysisJobAcceptedResponse> {
  return apiRequest(
    `/analysis/reanalysis/jobs/${jobId}/retry`,
    {
      method: "POST",
      body: JSON.stringify({
        pageSize,
      }),
    }
  );
}

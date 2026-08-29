import { expireAuthSession, getAccessToken, getIdToken } from "../auth/cognito";
import { frontendEnv } from "../config/env";
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
import {
  parseInsightsMoodsResponse,
  parseInsightsOverviewResponse,
  parseInsightsThemesResponse,
} from "./insightsValidation";
import type {
  InsightsReportResponse,
} from "../types/reports";
import {
  parseInsightsReportResponse,
} from "./reportsValidation";
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
import type {
  AccountEntitlementResponse,
} from "../types/accountEntitlement";
import type {
  OcrJobListResponse,
  OcrJobRetryResponse,
  OcrJobStatusFilter,
} from "../types/ocrJobs";
import type {
  AccountExportListResponse,
  AccountExportResponse,
} from "../types/accountExport";

const API_ENDPOINT = frontendEnv.apiEndpoint;
const DEVELOPMENT_IDENTITY_HEADERS: Record<string, string> =
  import.meta.env.VITE_APP_STAGE === "prod"
    ? {}
    : {
        "x-user-id":
          (import.meta.env.VITE_DEMO_USER_ID as string | undefined)
          || "demo-user",
      };

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

type ApiRequestOptions = RequestInit & {
  useIdentityToken?: boolean;
};

async function apiRequest<T>(
  path: string,
  options: ApiRequestOptions = {}
): Promise<T> {
  const { useIdentityToken, ...requestOptions } = options;
  const accessToken = useIdentityToken ? getIdToken() : getAccessToken();

  const response = await fetch(`${API_ENDPOINT}${path}`, {
    ...requestOptions,
    headers: {
      "content-type": "application/json",
      ...DEVELOPMENT_IDENTITY_HEADERS,
      ...(accessToken
        ? {
            Authorization: `Bearer ${accessToken}`,
          }
        : {}),
      ...(requestOptions.headers || {}),
    },
  });

  const data = await parseApiResponse(response);

  if (!response.ok) {
    if (response.status === 401) {
      expireAuthSession();
      throw new ApiRequestError(
        401,
        "Your secure session expired. Sign in again.",
        "Unauthorized",
        {}
      );
    }

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


export async function getAccountEntitlement():
Promise<AccountEntitlementResponse> {
  return apiRequest(
    "/account/entitlement"
  );
}

export async function requestAccountExport(
  requestToken: string,
  signal?: AbortSignal
): Promise<AccountExportResponse> {
  return apiRequest("/account/exports", {
    method: "POST",
    body: JSON.stringify({ requestToken }),
    signal,
    useIdentityToken: true,
  });
}

export async function listAccountExports(
  signal?: AbortSignal
): Promise<AccountExportListResponse> {
  return apiRequest("/account/exports", { signal, useIdentityToken: true });
}

export async function getAccountExport(
  exportId: string,
  signal?: AbortSignal
): Promise<AccountExportResponse> {
  return apiRequest(
    `/account/exports/${encodeURIComponent(exportId)}`,
    { signal, useIdentityToken: true }
  );
}


export type BillingCheckoutResponse = {
  checkout: {
    checkoutUrl: string;
  };
};


export async function createBillingCheckout(
  requestToken: string
): Promise<BillingCheckoutResponse> {
  return apiRequest(
    "/billing/checkout",
    {
      method: "POST",
      body: JSON.stringify({
        requestToken,
      }),
    }
  );
}


export type BillingPortalResponse = {
  portal: {
    billingPortalUrl: string;
  };
};


export async function createBillingPortal():
Promise<BillingPortalResponse> {
  return apiRequest(
    "/billing/portal",
    {
      method: "POST",
      body: JSON.stringify({}),
    }
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

export async function getEntry(entryId: string, signal?: AbortSignal): Promise<{ entry: JournalEntry }> {
  return apiRequest(`/entries/${encodeURIComponent(entryId)}`, { signal });
}

export async function analyzeEntry(entryId: string): Promise<{ message: string; entry: JournalEntry }> {
  return apiRequest(`/entries/${encodeURIComponent(entryId)}/analyze`, {
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
    `/entries/${encodeURIComponent(entryId)}/analysis-history?${query.toString()}`
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
  return apiRequest(`/entries/${encodeURIComponent(entryId)}/ocr`, {
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
  return apiRequest(`/entries/${encodeURIComponent(entryId)}/review`, {
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
  }>(`/entries/${encodeURIComponent(entryId)}`, {
    method: "DELETE",
  });
}


export async function getInsightsOverview(signal?: AbortSignal):
Promise<InsightsOverviewResponse> {
  return parseInsightsOverviewResponse(await apiRequest<unknown>(
    "/insights/overview",
    { signal }
  ));
}


export async function getInsightsThemes(signal?: AbortSignal):
Promise<InsightsThemesResponse> {
  return parseInsightsThemesResponse(await apiRequest<unknown>(
    "/insights/themes",
    { signal }
  ));
}


export async function getInsightsMoods(signal?: AbortSignal):
Promise<InsightsMoodsResponse> {
  return parseInsightsMoodsResponse(await apiRequest<unknown>(
    "/insights/moods",
    { signal }
  ));
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
  period?: string,
  signal?: AbortSignal,
): Promise<InsightsReportResponse> {
  return parseInsightsReportResponse(await apiRequest<unknown>(
    getReportPath(
      "/reports/weekly",
      period
    ),
    { signal }
  ), "WEEKLY", period);
}


export async function getMonthlyReport(
  period?: string,
  signal?: AbortSignal,
): Promise<InsightsReportResponse> {
  return parseInsightsReportResponse(await apiRequest<unknown>(
    getReportPath(
      "/reports/monthly",
      period
    ),
    { signal }
  ), "MONTHLY", period);
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
  signal,
}: {
  limit?: number;
  cursor?: string | null;
  signal?: AbortSignal;
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
    ),
    { signal }
  );
}


export async function getAskJm8History(
  historyId: string,
  signal?: AbortSignal,
): Promise<AskJm8HistoryDetailResponse> {
  return apiRequest(
    (
      "/insights/ask/history/"
      + encodeURIComponent(historyId)
    ),
    { signal }
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
  limit = 50,
  signal?: AbortSignal,
): Promise<HistoricalReanalysisJobListResponse> {
  const query = new URLSearchParams({
    status,
    limit: String(limit),
  });

  return apiRequest(
    `/analysis/reanalysis/jobs?${query.toString()}`,
    { signal }
  );
}

export async function getHistoricalReanalysisInventory(signal?: AbortSignal):
Promise<HistoricalReanalysisDryRunResponse> {
  return apiRequest(
    "/analysis/reanalysis/dry-run",
    { signal }
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

export async function listOcrJobs(
  status: OcrJobStatusFilter = "ALL",
  limit = 20,
  cursor?: string,
  signal?: AbortSignal,
): Promise<OcrJobListResponse> {
  const query = new URLSearchParams({
    status,
    limit: String(limit),
  });

  if (cursor) query.set("cursor", cursor);

  return apiRequest(`/ocr-jobs?${query.toString()}`, { signal });
}

export async function retryOcrJob(
  entryId: string
): Promise<OcrJobRetryResponse> {
  return apiRequest(
    `/entries/${encodeURIComponent(entryId)}/ocr/retry`,
    { method: "POST" }
  );
}

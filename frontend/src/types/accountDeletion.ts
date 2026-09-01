export type AccountDeletionStatus =
  | "REQUESTED"
  | "IN_PROGRESS"
  | "COMPLETED"
  | "FAILED";

export type AccountDeletionFailure = {
  code: string;
  retryable: boolean;
};

export type AccountDeletionRequest = {
  requestId: string;
  status: AccountDeletionStatus;
  requestedAt?: string;
  startedAt?: string;
  destructiveStartedAt?: string;
  completedAt?: string;
  failedAt?: string;
  failure?: AccountDeletionFailure;
};

export type AccountDeletionResponse = {
  deletionRequest: AccountDeletionRequest;
  replayed?: boolean;
};

export type AccountDeletionCreateResult = AccountDeletionResponse & {
  httpStatus: 200 | 202;
};

const REQUEST_ID_PATTERN = /^del_[a-f0-9]{32}$/;
const FAILURE_CODE_PATTERN = /^[A-Za-z][A-Za-z0-9_]{0,63}$/;
const UTC_TIMESTAMP_PATTERN = (
  /^\d{4}-\d{2}-\d{2}T\d{2}:\d{2}:\d{2}(?:\.\d{1,6})?Z$/
);
const STATUSES = new Set<AccountDeletionStatus>([
  "REQUESTED",
  "IN_PROGRESS",
  "COMPLETED",
  "FAILED",
]);

function optionalTimestamp(
  source: Record<string, unknown>,
  field: string,
): string | undefined {
  const value = source[field];
  return typeof value === "string" && UTC_TIMESTAMP_PATTERN.test(value)
    ? value
    : undefined;
}

export function parseAccountDeletionResponse(
  value: unknown,
): AccountDeletionResponse {
  if (!value || typeof value !== "object" || Array.isArray(value)) {
    throw new Error("Invalid account deletion response.");
  }
  const envelope = value as Record<string, unknown>;
  const rawRequest = envelope.deletionRequest;
  if (!rawRequest || typeof rawRequest !== "object" || Array.isArray(rawRequest)) {
    throw new Error("Invalid account deletion response.");
  }
  const source = rawRequest as Record<string, unknown>;
  const requestId = source.requestId;
  const status = source.status;
  if (
    typeof requestId !== "string"
    || !REQUEST_ID_PATTERN.test(requestId)
    || typeof status !== "string"
    || !STATUSES.has(status as AccountDeletionStatus)
  ) {
    throw new Error("Invalid account deletion response.");
  }

  const request: AccountDeletionRequest = {
    requestId,
    status: status as AccountDeletionStatus,
  };
  for (const field of (
    [
      "requestedAt",
      "startedAt",
      "destructiveStartedAt",
      "completedAt",
      "failedAt",
    ] as const
  )) {
    const text = optionalTimestamp(source, field);
    if (text) request[field] = text;
  }

  if (request.status === "FAILED") {
    const rawFailure = source.failure;
    if (rawFailure && typeof rawFailure === "object" && !Array.isArray(rawFailure)) {
      const failure = rawFailure as Record<string, unknown>;
      request.failure = {
        code: typeof failure.code === "string"
          && FAILURE_CODE_PATTERN.test(failure.code)
          ? failure.code
          : "DeletionFailed",
        retryable: failure.retryable === true,
      };
    }
  }

  return {
    deletionRequest: request,
    ...(typeof envelope.replayed === "boolean"
      ? { replayed: envelope.replayed }
      : {}),
  };
}

export function hasObservedDestructiveDeletion(
  request: AccountDeletionRequest | null,
): boolean {
  return Boolean(
    request
    && request.status !== "REQUESTED"
    && request.destructiveStartedAt,
  );
}

export function shouldTreatTrackingAuthLossAsProcessed(
  httpStatus: number | undefined,
  destructiveDeletionObserved: boolean,
): boolean {
  return httpStatus === 401 && destructiveDeletionObserved;
}

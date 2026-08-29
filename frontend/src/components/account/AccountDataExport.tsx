import { Database, Download, RefreshCw } from "lucide-react";
import { useCallback, useEffect, useRef, useState } from "react";
import {
  getAccountExport,
  listAccountExports,
  requestAccountExport,
} from "../../api/client";
import { frontendEnv } from "../../config/env";
import {
  expectedExportDownloadHostname,
  isTrustedExportDownloadUrl,
} from "../../security/exportDownloadUrls";
import type { AccountExportJob } from "../../types/accountExport";
import "./AccountDataExport.css";

const POLL_INTERVAL_MS = 3_000;

type AccountDataExportProps = {
  isPanelOpen: boolean;
};

function isPending(job: AccountExportJob | null): boolean {
  return job?.status === "QUEUED" || job?.status === "RUNNING";
}

function statusMessage(job: AccountExportJob | null): string {
  switch (job?.status) {
    case "QUEUED":
      return "Your export is queued.";
    case "RUNNING":
      return "JM8 is preparing your private package.";
    case "COMPLETED":
      return "The package was prepared. Verifying the secure download…";
    case "FAILED":
      return job.error?.message || "The export could not be completed. You can try again.";
    case "EXPIRED":
      return "This package expired. Request a new export when you are ready.";
    default:
      return "Request a ZIP package containing your JM8 account data.";
  }
}

function readableSize(bytes: number | undefined): string | null {
  if (typeof bytes !== "number" || bytes < 0) return null;
  return new Intl.NumberFormat([], {
    style: "unit",
    unit: bytes >= 1_048_576 ? "megabyte" : "kilobyte",
    unitDisplay: "short",
    maximumFractionDigits: 1,
  }).format(bytes / (bytes >= 1_048_576 ? 1_048_576 : 1_024));
}

export default function AccountDataExport({
  isPanelOpen,
}: AccountDataExportProps) {
  const [job, setJob] = useState<AccountExportJob | null>(null);
  const [errorMessage, setErrorMessage] = useState("");
  const [isRequesting, setIsRequesting] = useState(false);
  const generationRef = useRef(0);
  const controllerRef = useRef<AbortController | null>(null);
  const timerRef = useRef<number | null>(null);

  const stop = useCallback(() => {
    generationRef.current += 1;
    controllerRef.current?.abort();
    controllerRef.current = null;
    if (timerRef.current !== null) {
      window.clearTimeout(timerRef.current);
      timerRef.current = null;
    }
    setIsRequesting(false);
  }, []);

  const loadDetail = useCallback(async (
    exportId: string,
    generation: number
  ): Promise<void> => {
    const controller = new AbortController();
    controllerRef.current?.abort();
    controllerRef.current = controller;
    try {
      const response = await getAccountExport(exportId, controller.signal);
      if (generation !== generationRef.current || controller.signal.aborted) return;
      setJob(response.export);
      setErrorMessage("");
      if (isPending(response.export)) {
        timerRef.current = window.setTimeout(() => {
          void loadDetail(exportId, generation);
        }, POLL_INTERVAL_MS);
      }
    } catch (error) {
      if (controller.signal.aborted || generation !== generationRef.current) return;
      setErrorMessage(
        error instanceof Error
          ? error.message
          : "Export status is temporarily unavailable."
      );
    }
  }, []);

  useEffect(() => {
    if (!isPanelOpen) {
      stop();
      return;
    }
    const generation = generationRef.current + 1;
    generationRef.current = generation;
    const controller = new AbortController();
    controllerRef.current = controller;
    void listAccountExports(controller.signal)
      .then((response) => {
        if (controller.signal.aborted || generation !== generationRef.current) return;
        const latest = response.exports[0] || null;
        setJob(latest);
        setErrorMessage("");
        if (latest) void loadDetail(latest.exportId, generation);
      })
      .catch((error: unknown) => {
        if (controller.signal.aborted || generation !== generationRef.current) return;
        setErrorMessage(
          error instanceof Error
            ? error.message
            : "Export history is temporarily unavailable."
        );
      });
    return stop;
  }, [isPanelOpen, loadDetail, stop]);

  async function handleRequest(): Promise<void> {
    if (isRequesting || isPending(job)) return;
    stop();
    const generation = generationRef.current;
    const controller = new AbortController();
    controllerRef.current = controller;
    setIsRequesting(true);
    setErrorMessage("");
    try {
      const response = await requestAccountExport(
        crypto.randomUUID(),
        controller.signal
      );
      if (controller.signal.aborted || generation !== generationRef.current) return;
      setJob(response.export);
      if (isPending(response.export)) {
        timerRef.current = window.setTimeout(() => {
          void loadDetail(response.export.exportId, generation);
        }, POLL_INTERVAL_MS);
      }
    } catch (error) {
      if (controller.signal.aborted || generation !== generationRef.current) return;
      setErrorMessage(
        error instanceof Error
          ? error.message
          : "The export request could not be started."
      );
    } finally {
      if (generation === generationRef.current) setIsRequesting(false);
    }
  }

  const expectedHost = expectedExportDownloadHostname(frontendEnv.appStage);
  const trustedDownload = job?.status === "COMPLETED"
    && typeof job.downloadUrl === "string"
    && isTrustedExportDownloadUrl(job.downloadUrl, expectedHost)
    ? job.downloadUrl
    : null;
  const packageSize = readableSize(job?.fileSizeBytes);

  return (
    <section className="account-data-export" aria-labelledby="account-data-export-heading">
      <header>
        <span className="account-data-export-icon" aria-hidden="true">
          <Database size={18} />
        </span>
        <div>
          <p>Your data</p>
          <h2 id="account-data-export-heading">Account export</h2>
        </div>
      </header>
      <p className="account-data-export-copy">
        Packages can take time to prepare and expire 24 hours after completion.
      </p>
      <div className="account-data-export-status" role="status" aria-live="polite">
        <strong>{job?.status || "NOT REQUESTED"}</strong>
        <span>
          {errorMessage || (trustedDownload
            ? "Your package is ready to download."
            : statusMessage(job))}
        </span>
        {packageSize && job?.status === "COMPLETED" ? <small>{packageSize}</small> : null}
      </div>
      <div className="account-data-export-actions">
        <button
          type="button"
          onClick={() => void handleRequest()}
          disabled={isRequesting || isPending(job)}
        >
          {isRequesting || isPending(job) ? <RefreshCw size={15} className="account-data-export-spin" /> : <Database size={15} />}
          {isRequesting ? "Requesting…" : isPending(job) ? "Preparing…" : "Request data export"}
        </button>
        {trustedDownload ? (
          <a href={trustedDownload} download={job?.fileName || "jm8-export.zip"}>
            <Download size={15} /> Download
          </a>
        ) : null}
      </div>
    </section>
  );
}

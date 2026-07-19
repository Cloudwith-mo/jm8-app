import {
  useCallback,
  useEffect,
  useMemo,
  useRef,
  useState,
} from "react";
import {
  History,
  RefreshCw,
} from "lucide-react";
import { getAnalysisHistory } from "../../api/client";
import type {
  AnalysisHistoryVersion,
} from "../../types/journal";

type AnalysisHistoryCardProps = {
  entryId?: string;
  versionCount?: number;
  disabled?: boolean;
};

function formatDate(value?: string) {
  if (!value) return "Unknown date";

  const date = new Date(value);

  if (Number.isNaN(date.getTime())) {
    return "Unknown date";
  }

  return date.toLocaleString([], {
    month: "short",
    day: "numeric",
    year: "numeric",
    hour: "numeric",
    minute: "2-digit",
  });
}

function formatLabel(value?: string) {
  if (!value) return "Unknown";

  return value
    .replaceAll("_", " ")
    .replace(
      /\\b\\w/g,
      (letter) => letter.toUpperCase()
    );
}

function getErrorMessage(error: unknown) {
  return error instanceof Error
    ? error.message
    : "Analysis history could not be loaded.";
}

export default function AnalysisHistoryCard({
  entryId,
  versionCount = 0,
  disabled = false,
}: AnalysisHistoryCardProps) {
  const [versions, setVersions] = useState<
    AnalysisHistoryVersion[]
  >([]);
  const [
    selectedVersionId,
    setSelectedVersionId,
  ] = useState<string | null>(null);
  const [isLoading, setIsLoading] =
    useState(false);
  const [error, setError] =
    useState<string | null>(null);

  const requestSequence = useRef(0);

  const loadHistory = useCallback(async () => {
    const requestId =
      requestSequence.current + 1;

    requestSequence.current = requestId;

    if (!entryId) {
      setVersions([]);
      setSelectedVersionId(null);
      setError(null);
      setIsLoading(false);
      return;
    }

    setIsLoading(true);
    setError(null);

    try {
      const result = await getAnalysisHistory(
        entryId,
        20
      );

      if (
        requestSequence.current !== requestId
      ) {
        return;
      }

      setVersions(result.versions);

      setSelectedVersionId(
        (currentVersionId) => {
          const currentStillExists =
            result.versions.some(
              (version) =>
                version.analysisVersionId ===
                currentVersionId
            );

          if (currentStillExists) {
            return currentVersionId;
          }

          return (
            result.versions[0]
              ?.analysisVersionId || null
          );
        }
      );
    } catch (loadError) {
      if (
        requestSequence.current !== requestId
      ) {
        return;
      }

      setVersions([]);
      setSelectedVersionId(null);
      setError(
        getErrorMessage(loadError)
      );
    } finally {
      if (
        requestSequence.current === requestId
      ) {
        setIsLoading(false);
      }
    }
  }, [entryId]);

  useEffect(() => {
    void loadHistory();
  }, [
    loadHistory,
    versionCount,
  ]);

  const selectedVersion = useMemo(
    () =>
      versions.find(
        (version) =>
          version.analysisVersionId ===
          selectedVersionId
      ) || versions[0],
    [
      versions,
      selectedVersionId,
    ]
  );

  const selectedAnalysis =
    selectedVersion?.analysis;

  return (
    <section className="analysis-history-card">
      <div className="analysis-history-heading">
        <div>
          <p>
            <History size={16} />
            Analysis History
          </p>

          <h3>
            {versions.length === 1
              ? "1 saved version"
              : `${versions.length} saved versions`}
          </h3>
        </div>

        <button
          type="button"
          onClick={() => void loadHistory()}
          disabled={
            !entryId ||
            disabled ||
            isLoading
          }
          aria-label="Refresh analysis history"
        >
          <RefreshCw
            size={16}
            className={
              isLoading
                ? "history-refreshing"
                : ""
            }
          />
        </button>
      </div>

      {isLoading ? (
        <div className="analysis-history-state">
          <span className="history-loading-dot" />
          <p>Loading saved analyses...</p>
        </div>
      ) : error ? (
        <div className="analysis-history-state error">
          <p>{error}</p>

          <button
            type="button"
            onClick={() => void loadHistory()}
            disabled={!entryId || disabled}
          >
            Try Again
          </button>
        </div>
      ) : versions.length === 0 ? (
        <div className="analysis-history-state">
          <History size={21} />
          <p>
            Previous analyses will appear here
            after this entry is analyzed.
          </p>
        </div>
      ) : (
        <>
          <div className="analysis-history-list">
            {versions.map(
              (version, index) => {
                const isActive =
                  version.analysisVersionId ===
                  selectedVersion
                    ?.analysisVersionId;

                return (
                  <button
                    type="button"
                    key={
                      version.analysisVersionId
                    }
                    className={
                      isActive
                        ? "analysis-history-version active"
                        : "analysis-history-version"
                    }
                    onClick={() =>
                      setSelectedVersionId(
                        version.analysisVersionId
                      )
                    }
                  >
                    <strong>
                      {index === 0
                        ? "Latest"
                        : `Previous ${index}`}
                    </strong>

                    <span>
                      {formatDate(
                        version.analysisCompletedAt ||
                          version.createdAt
                      )}
                    </span>
                  </button>
                );
              }
            )}
          </div>

          {selectedVersion && (
            <div className="history-version-detail">
              <div className="history-version-meta">
                <div>
                  <span>
                    {selectedVersion ===
                    versions[0]
                      ? "Current version"
                      : "Previous version"}
                  </span>

                  <strong>
                    {formatDate(
                      selectedVersion
                        .analysisCompletedAt ||
                        selectedVersion
                          .createdAt
                    )}
                  </strong>
                </div>

                <em>
                  {formatLabel(
                    selectedVersion
                      .analysisSource
                  )}
                </em>
              </div>

              <div className="history-metric-grid">
                <div>
                  <span>Mood</span>
                  <strong>
                    {formatLabel(
                      selectedAnalysis?.mood
                    )}
                  </strong>
                </div>

                <div>
                  <span>Sentiment</span>
                  <strong>
                    {formatLabel(
                      selectedAnalysis
                        ?.sentiment
                    )}
                  </strong>
                </div>
              </div>

              <div className="history-detail-block">
                <h4>Summary</h4>
                <p>
                  {selectedAnalysis?.summary ||
                    "No summary was stored for this version."}
                </p>
              </div>

              {(selectedAnalysis?.themes || [])
                .length > 0 && (
                <div className="history-theme-list">
                  {selectedAnalysis?.themes?.map(
                    (theme) => (
                      <span key={theme}>
                        {theme}
                      </span>
                    )
                  )}
                </div>
              )}

              <div className="history-detail-block next-step">
                <h4>Next Step</h4>
                <p>
                  {selectedAnalysis?.nextStep ||
                    "No next step was stored for this version."}
                </p>
              </div>
            </div>
          )}
        </>
      )}
    </section>
  );
}

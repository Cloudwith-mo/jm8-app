import sys

from llm_journal_analyzer import (
    AnalyzerError,
    analyze_journal_entry_llm,
)


SYNTHETIC_ENTRY = """
Last month I worried constantly about failing at work.
Today I feel calmer and I am not anxious anymore.

I still have uncertainty about launching my fictional
side project, but I am proud that I have shown up every
day. My goal is to complete one prototype this week
without sacrificing sleep.
""".strip()


def main() -> int:
    try:
        result = analyze_journal_entry_llm(
            SYNTHETIC_ENTRY
        )
    except AnalyzerError as exc:
        print("LLM analyzer test: failed")
        print("Safe error:", str(exc))
        return 1

    print("LLM analyzer test: succeeded")
    print("Analyzer type:", result["analyzerType"])
    print("Provider:", result["provider"])
    print("Schema version:", result["schemaVersion"])
    print("Prompt version:", result["promptVersion"])
    print("Sentiment:", result["sentiment"])
    print(
        "Sentiment confidence:",
        result["sentimentConfidence"],
    )
    print("Primary mood:", result["mood"])
    print(
        "Secondary moods:",
        ", ".join(result["secondaryMoods"])
        or "none",
    )
    print(
        "Themes:",
        ", ".join(result["themes"]),
    )
    print(
        "Emerging topics:",
        ", ".join(result["emergingTopics"])
        or "none",
    )
    print("Summary:", result["summary"])
    print(
        "Detected challenges:",
        len(result["challenges"]),
    )
    print(
        "Detected goals:",
        len(result["goals"]),
    )
    print(
        "Growth signals:",
        len(result["growthSignals"]),
    )
    print(
        "Behavior patterns:",
        len(result["behaviorPatterns"]),
    )
    print(
        "Input tokens:",
        result["usage"]["inputTokens"],
    )
    print(
        "Output tokens:",
        result["usage"]["outputTokens"],
    )
    print(
        "Total tokens:",
        result["usage"]["totalTokens"],
    )
    print(
        "Latency ms:",
        result["usage"]["latencyMs"],
    )

    return 0


if __name__ == "__main__":
    sys.exit(main())

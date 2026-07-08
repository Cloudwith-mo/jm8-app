from datetime import datetime, timezone
from typing import Any


POSITIVE_WORDS = {
    "focused", "grateful", "strong", "clear", "happy", "peaceful",
    "disciplined", "confident", "proud", "hopeful", "energized"
}

NEGATIVE_WORDS = {
    "tired", "angry", "sad", "stressed", "anxious", "worried",
    "afraid", "lost", "confused", "frustrated", "overwhelmed"
}

THEME_KEYWORDS = {
    "discipline": ["discipline", "consistent", "routine", "focus", "training"],
    "career": ["job", "interview", "cloud", "aws", "career", "resume"],
    "faith": ["allah", "dua", "prayer", "muslim", "faith", "islam"],
    "fitness": ["run", "gym", "workout", "weight", "cardio", "basketball"],
    "growth": ["grow", "growth", "improve", "better", "learn", "evolve"],
    "stress": ["stress", "pressure", "overwhelmed", "anxious", "worried"],
}


def analyze_journal_entry(text: str) -> dict[str, Any]:
    if not text or not text.strip():
        return {
            "status": "EMPTY_INPUT",
            "message": "No journal text was provided."
        }

    normalized = text.lower()
    words = [word.strip(".,!?;:()[]{}\"'").lower() for word in normalized.split()]

    positive_count = sum(1 for word in words if word in POSITIVE_WORDS)
    negative_count = sum(1 for word in words if word in NEGATIVE_WORDS)

    if positive_count > negative_count:
        sentiment = "positive"
    elif negative_count > positive_count:
        sentiment = "negative"
    else:
        sentiment = "neutral"

    themes: list[str] = []
    for theme, keywords in THEME_KEYWORDS.items():
        if any(keyword in normalized for keyword in keywords):
            themes.append(theme)

    if not themes:
        themes = ["general_reflection"]

    if sentiment == "positive":
        mood = "encouraged"
    elif sentiment == "negative":
        mood = "heavy"
    else:
        mood = "reflective"

    return {
        "status": "ANALYZED",
        "analyzedAt": datetime.now(timezone.utc).isoformat(),
        "wordCount": len(words),
        "sentiment": sentiment,
        "mood": mood,
        "positiveSignalCount": positive_count,
        "negativeSignalCount": negative_count,
        "themes": themes,
        "summary": build_summary(sentiment, mood, themes),
        "nextStep": build_next_step(sentiment, themes)
    }


def build_summary(sentiment: str, mood: str, themes: list[str]) -> str:
    theme_text = ", ".join(themes)
    return (
        f"This entry appears {sentiment} overall, with a {mood} tone. "
        f"The main themes detected were: {theme_text}."
    )


def build_next_step(sentiment: str, themes: list[str]) -> str:
    if sentiment == "negative":
        return "Write one small action you can take today to reduce pressure and regain control."
    if "discipline" in themes:
        return "Turn this reflection into one repeatable habit for tomorrow."
    if "career" in themes:
        return "Identify the next concrete career move: apply, study, build, or follow up."
    return "Capture one lesson from this entry and one action for the next 24 hours."

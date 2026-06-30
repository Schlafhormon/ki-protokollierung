"""
Summarization module for generating meeting minutes per TOP.

Uses an OpenAI-compatible API, typically Ollama, for local German
summarization. The public API still exposes an editable text summary, while
the backend internally works with structured minutes fields.

Configuration via environment variables:
- LLM_BASE_URL: API endpoint (default: http://localhost:11434/v1 for Ollama)
- LLM_MODEL: Model name (default: qwen3:8b)
- LLM_TIMEOUT_SECONDS: request timeout per LLM call (default: 120)
- LLM_MAX_RETRIES: retry count for transient LLM errors (default: 2)
- LLM_CHUNK_CHARS: target chunk size for long TOP transcripts (default: 12000)
- LLM_STRUCTURED_FALLBACK: free-text fallback on structured failure (default: true)
"""

import json
import os
import re
import time
from dataclasses import asdict, dataclass, field
from typing import Any, Optional

# LLM server configuration (Ollama)
LLM_BASE_URL = os.environ.get("LLM_BASE_URL", "http://localhost:11434/v1")
LLM_MODEL = os.environ.get("LLM_MODEL", "qwen3:8b")
LLM_API_KEY = os.environ.get("LLM_API_KEY", "ollama")
LLM_TIMEOUT_SECONDS = float(os.environ.get("LLM_TIMEOUT_SECONDS", "120"))
LLM_MAX_RETRIES = int(os.environ.get("LLM_MAX_RETRIES", "2"))
LLM_RETRY_BACKOFF_SECONDS = float(os.environ.get("LLM_RETRY_BACKOFF_SECONDS", "0.5"))
LLM_CHUNK_CHARS = int(os.environ.get("LLM_CHUNK_CHARS", "12000"))
LLM_STRUCTURED_FALLBACK = (
    os.environ.get("LLM_STRUCTURED_FALLBACK", "true").lower() != "false"
)


@dataclass
class StructuredSummary:
    """Structured internal representation of one TOP summary."""

    discussion: list[str] = field(default_factory=list)
    decisions: list[str] = field(default_factory=list)
    votes: list[str] = field(default_factory=list)
    action_items: list[str] = field(default_factory=list)
    open_points: list[str] = field(default_factory=list)
    uncertainties: list[str] = field(default_factory=list)

    def to_dict(self) -> dict[str, list[str]]:
        return asdict(self)


@dataclass
class SummarizationResult:
    """Result from summarization including timing and internal structure."""

    summary: str
    duration_seconds: float
    structured: StructuredSummary | None = None
    fallback_used: bool = False
    chunks_processed: int = 1


@dataclass
class LLMErrorInfo:
    """Classified LLM error metadata for retries and API diagnostics."""

    category: str
    transient: bool


class LLMCallError(RuntimeError):
    """Raised when an LLM call failed after classification/retries."""

    def __init__(self, message: str, *, category: str, transient: bool) -> None:
        super().__init__(message)
        self.category = category
        self.transient = transient


class StructuredOutputError(ValueError):
    """Raised when the model did not return usable structured JSON."""


# Default system prompt for structured municipal meeting summarization.
DEFAULT_SYSTEM_PROMPT = """Du bist ein Experte für Niederschriften deutscher kommunaler Gremien
(Rat, Ausschuss, Bezirksvertretung, Ortsbeirat).

Arbeite protokollarisch, sachlich und verwaltungsnah:
- keine wörtlichen Zitate, sondern präzise Paraphrasen
- dritte Person und formale Verwaltungssprache
- keine Ausschmückungen, keine rechtliche Bewertung über das Transkript hinaus
- Beschlüsse, Abstimmungen und Aufträge nur aufnehmen, wenn sie aus dem Transkript hervorgehen
- fehlende oder unklare Informationen ausdrücklich unter "uncertainties" markieren
- Geschäftsordnungs- und Technikdetails nur aufnehmen, wenn sie für den TOP relevant sind

Gib ausschließlich valides JSON mit genau diesen Schlüsseln zurück:
{
  "discussion": ["wesentliche Diskussionspunkte, Sachverhalte, Argumente und Positionen"],
  "decisions": ["Beschlüsse oder Einigungen"],
  "votes": ["Abstimmungsergebnisse mit Stimmenzahlen, Enthaltungen oder Einstimmigkeit"],
  "action_items": ["vereinbarte Maßnahmen, Prüfaufträge, Zuständigkeiten oder Fristen"],
  "open_points": ["offene Fragen, weiterer Beratungsbedarf oder Vertagungen"],
  "uncertainties": ["fachlich relevante Unsicherheiten der Auswertung"]
}

Jeder Wert ist eine Liste kurzer, vollständiger deutscher Sätze. Wenn eine
Kategorie im Transkript nicht vorkommt, nutze eine leere Liste. Keine Markdown-
Formatierung und kein Text außerhalb des JSON-Objekts."""


FREETEXT_SYSTEM_PROMPT = """Du bist ein Experte für die Erstellung von Sitzungsprotokollen
für deutsche Kommunalverwaltungen.

Erstelle aus dem Transkript eines Tagesordnungspunktes eine fachlich präzise
Zusammenfassung im Stil einer offiziellen Niederschrift.

STIL:
- Formale Verwaltungssprache, dritte Person
- Paraphrasieren statt wörtlich zitieren
- Direkt mit Inhalt beginnen, keine Einleitung

INHALT:
- Wesentliche Diskussionspunkte und Argumente
- Getroffene Beschlüsse und erkennbare Abstimmungsergebnisse
- Wichtige Positionen der Teilnehmenden
- Vereinbarte Maßnahmen, Prüfaufträge, offene Punkte und Unsicherheiten

IGNORIEREN:
- Füllwörter, Versprecher, triviale Zwischenbemerkungen
- Mikrofon-, Redezeit- und Technikdetails ohne fachliche Relevanz

FORMAT:
- 2 bis 5 knappe Absätze
- NUR Fließtext, KEINE Markdown-Formatierung"""


STRUCTURED_KEYS = {
    "discussion",
    "decisions",
    "votes",
    "action_items",
    "open_points",
    "uncertainties",
}

KEY_ALIASES = {
    "diskussion": "discussion",
    "beschluss": "decisions",
    "beschluesse": "decisions",
    "beschlüsse": "decisions",
    "abstimmung": "votes",
    "abstimmungen": "votes",
    "massnahmen": "action_items",
    "maßnahmen": "action_items",
    "offene_punkte": "open_points",
    "unsicherheiten": "uncertainties",
}


def build_structured_system_prompt(system_prompt: str | None) -> str:
    """Keep the structured JSON contract even when the UI sends legacy prompts."""

    custom_prompt = (system_prompt or "").strip()
    if not custom_prompt or custom_prompt == DEFAULT_SYSTEM_PROMPT.strip():
        return DEFAULT_SYSTEM_PROMPT

    return (
        DEFAULT_SYSTEM_PROMPT
        + "\n\nZusätzliche fachliche Vorgaben des Nutzers. Diese Vorgaben nur "
        "anwenden, soweit sie dem JSON-Schema und der strukturierten Ausgabe "
        "oben nicht widersprechen; das JSON-Ausgabeformat hat Vorrang:\n"
        + custom_prompt
    )


def _load_openai_client() -> Any:
    try:
        from openai import OpenAI
    except ImportError:
        raise RuntimeError(
            "OpenAI client nicht installiert. Installieren Sie mit: uv add openai"
        )

    return OpenAI(
        base_url=LLM_BASE_URL,
        api_key=LLM_API_KEY,
        timeout=LLM_TIMEOUT_SECONDS,
    )


def classify_llm_error(error: Exception) -> LLMErrorInfo:
    """Classify common OpenAI-compatible client errors."""

    status_code = getattr(error, "status_code", None)
    name = error.__class__.__name__.lower()
    message = str(error).lower()

    if "timeout" in name or "timed out" in message or "timeout" in message:
        return LLMErrorInfo("timeout", True)
    if "rate" in name or status_code == 429:
        return LLMErrorInfo("rate_limit", True)
    if "connection" in name or "network" in name or "connect" in message:
        return LLMErrorInfo("network", True)
    if status_code in {408, 409, 425} or (
        isinstance(status_code, int) and status_code >= 500
    ):
        return LLMErrorInfo("server", True)
    if isinstance(status_code, int) and 400 <= status_code < 500:
        return LLMErrorInfo("client", False)
    return LLMErrorInfo("unknown", False)


def _chat_completion_content(
    client: Any,
    *,
    model: str,
    messages: list[dict[str, str]],
    max_tokens: int,
    temperature: float,
) -> str:
    last_error: Exception | None = None
    last_info = LLMErrorInfo("unknown", False)

    for attempt in range(LLM_MAX_RETRIES + 1):
        try:
            response = client.chat.completions.create(
                model=model,
                messages=messages,
                max_tokens=max_tokens,
                temperature=temperature,
                timeout=LLM_TIMEOUT_SECONDS,
            )
            content = response.choices[0].message.content or ""
            if not content.strip():
                raise LLMCallError(
                    "Leere Antwort des LLM",
                    category="empty_response",
                    transient=False,
                )
            return content.strip()
        except LLMCallError:
            raise
        except Exception as error:
            last_error = error
            last_info = classify_llm_error(error)
            if not last_info.transient or attempt >= LLM_MAX_RETRIES:
                break
            time.sleep(LLM_RETRY_BACKOFF_SECONDS * (2**attempt))

    raise LLMCallError(
        f"LLM-Aufruf fehlgeschlagen ({last_info.category}): {last_error}",
        category=last_info.category,
        transient=last_info.transient,
    )


def _extract_json_object(content: str) -> dict[str, Any]:
    stripped = content.strip()
    fence_match = re.search(r"```(?:json)?\s*(.*?)```", stripped, re.DOTALL | re.I)
    if fence_match:
        stripped = fence_match.group(1).strip()

    if not stripped.startswith("{"):
        start = stripped.find("{")
        end = stripped.rfind("}")
        if start >= 0 and end > start:
            stripped = stripped[start : end + 1]

    try:
        parsed = json.loads(stripped)
    except json.JSONDecodeError as error:
        raise StructuredOutputError(f"Keine valide JSON-Antwort: {error}") from error

    if not isinstance(parsed, dict):
        raise StructuredOutputError("Strukturierte Antwort ist kein JSON-Objekt")
    return parsed


def _normalize_items(value: Any) -> list[str]:
    if value is None:
        return []
    if isinstance(value, str):
        value = [value]
    if not isinstance(value, list):
        return []

    items = []
    for item in value:
        if item is None:
            continue
        text = str(item).strip()
        if text:
            items.append(text)
    return items


def parse_structured_summary(content: str) -> StructuredSummary:
    parsed = _extract_json_object(content)
    normalized: dict[str, list[str]] = {key: [] for key in STRUCTURED_KEYS}

    for raw_key, raw_value in parsed.items():
        key = str(raw_key).strip()
        normalized_key = KEY_ALIASES.get(key.lower(), key)
        if normalized_key in STRUCTURED_KEYS:
            normalized[normalized_key] = _normalize_items(raw_value)

    if not any(normalized.values()):
        raise StructuredOutputError("Strukturierte Antwort enthält keine Inhalte")

    return StructuredSummary(**normalized)


def render_structured_summary(structured: StructuredSummary) -> str:
    """Render structured minutes into editable text for existing users."""

    sections = [
        ("Diskussion", structured.discussion),
        ("Beschluss", structured.decisions),
        ("Abstimmung", structured.votes),
        ("Maßnahmen/offene Punkte", structured.action_items + structured.open_points),
        ("Unsicherheiten", structured.uncertainties),
    ]
    rendered_sections = []
    for title, items in sections:
        clean_items = [item.strip() for item in items if item.strip()]
        if not clean_items:
            continue
        rendered_sections.append(f"{title}:\n" + "\n".join(clean_items))
    return "\n\n".join(rendered_sections).strip()


def split_transcript_into_chunks(
    transcript_text: str,
    *,
    max_chars: int | None = None,
) -> list[str]:
    """Split long transcripts on line boundaries for map-reduce summarization."""

    max_chars = max_chars or LLM_CHUNK_CHARS
    text = transcript_text.strip()
    if not text:
        return []
    if len(text) <= max_chars:
        return [text]

    chunks: list[str] = []
    current: list[str] = []
    current_chars = 0

    for line in text.splitlines():
        line = line.rstrip()
        line_len = len(line) + 1
        if current and current_chars + line_len > max_chars:
            chunks.append("\n".join(current).strip())
            current = []
            current_chars = 0

        if line_len > max_chars:
            for start in range(0, len(line), max_chars):
                part = line[start : start + max_chars].strip()
                if part:
                    chunks.append(part)
            continue

        current.append(line)
        current_chars += line_len

    if current:
        chunks.append("\n".join(current).strip())
    return [chunk for chunk in chunks if chunk]


def _structured_user_prompt(
    top_title: str,
    transcript_text: str,
    *,
    chunk_index: int | None = None,
    chunk_count: int | None = None,
) -> str:
    chunk_note = ""
    if chunk_index is not None and chunk_count is not None:
        chunk_note = (
            f"\nDies ist Teil {chunk_index + 1} von {chunk_count}. "
            "Extrahiere nur Informationen, die in diesem Teil vorkommen."
        )

    return f"""Erstelle strukturierte Protokollnotizen für folgenden Tagesordnungspunkt.{chunk_note}

TOP: {top_title}

Transkript:
{transcript_text}

JSON:"""


def _reduce_user_prompt(top_title: str, partials: list[StructuredSummary]) -> str:
    partial_json = json.dumps(
        [partial.to_dict() for partial in partials],
        ensure_ascii=False,
        indent=2,
    )
    return f"""Führe die folgenden strukturierten Teilnotizen zu einer konsolidierten,
dublettenfreien Protokollzusammenfassung zusammen. Erhalte fachlich relevante
Unschärfen, erfinde keine Beschlüsse und keine Abstimmungsergebnisse.

TOP: {top_title}

Teilnotizen:
{partial_json}

JSON:"""


def _summarize_structured(
    client: Any,
    *,
    top_title: str,
    transcript_text: str,
    model: str,
    system_prompt: str,
) -> tuple[StructuredSummary, int]:
    chunks = split_transcript_into_chunks(transcript_text)
    if not chunks:
        raise StructuredOutputError("Kein Transkripttext vorhanden")

    if len(chunks) == 1:
        content = _chat_completion_content(
            client,
            model=model,
            messages=[
                {"role": "system", "content": system_prompt},
                {
                    "role": "user",
                    "content": _structured_user_prompt(top_title, chunks[0]),
                },
            ],
            max_tokens=1400,
            temperature=0.2,
        )
        return parse_structured_summary(content), 1

    partials = []
    for chunk_index, chunk in enumerate(chunks):
        content = _chat_completion_content(
            client,
            model=model,
            messages=[
                {"role": "system", "content": system_prompt},
                {
                    "role": "user",
                    "content": _structured_user_prompt(
                        top_title,
                        chunk,
                        chunk_index=chunk_index,
                        chunk_count=len(chunks),
                    ),
                },
            ],
            max_tokens=1200,
            temperature=0.2,
        )
        partials.append(parse_structured_summary(content))

    content = _chat_completion_content(
        client,
        model=model,
        messages=[
            {"role": "system", "content": system_prompt},
            {"role": "user", "content": _reduce_user_prompt(top_title, partials)},
        ],
        max_tokens=1800,
        temperature=0.2,
    )
    return parse_structured_summary(content), len(chunks)


def _freetext_user_prompt(top_title: str, transcript_text: str) -> str:
    return f"""Erstelle eine Zusammenfassung für folgenden Tagesordnungspunkt:

TOP: {top_title}

Transkript:
{transcript_text}

Zusammenfassung:"""


def _reduce_freetext_prompt(top_title: str, partials: list[str]) -> str:
    joined = "\n\n".join(
        f"Teilzusammenfassung {index + 1}:\n{partial}"
        for index, partial in enumerate(partials)
    )
    return f"""Führe die folgenden Teilzusammenfassungen zu einer konsolidierten
Niederschrift für den Tagesordnungspunkt zusammen. Entferne Dopplungen und
erfinde keine Beschlüsse, Abstimmungen oder Zuständigkeiten.

TOP: {top_title}

{joined}

Zusammenfassung:"""


def _summarize_freetext(
    client: Any,
    *,
    top_title: str,
    transcript_text: str,
    model: str,
) -> tuple[str, int]:
    chunks = split_transcript_into_chunks(transcript_text)
    if not chunks:
        return "", 0

    if len(chunks) == 1:
        content = _chat_completion_content(
            client,
            model=model,
            messages=[
                {"role": "system", "content": FREETEXT_SYSTEM_PROMPT},
                {"role": "user", "content": _freetext_user_prompt(top_title, chunks[0])},
            ],
            max_tokens=1024,
            temperature=0.3,
        )
        return content, 1

    partials = []
    for chunk in chunks:
        partials.append(
            _chat_completion_content(
                client,
                model=model,
                messages=[
                    {"role": "system", "content": FREETEXT_SYSTEM_PROMPT},
                    {
                        "role": "user",
                        "content": _freetext_user_prompt(top_title, chunk),
                    },
                ],
                max_tokens=900,
                temperature=0.3,
            )
        )

    content = _chat_completion_content(
        client,
        model=model,
        messages=[
            {"role": "system", "content": FREETEXT_SYSTEM_PROMPT},
            {"role": "user", "content": _reduce_freetext_prompt(top_title, partials)},
        ],
        max_tokens=1400,
        temperature=0.3,
    )
    return content, len(chunks)


def summarize_segment(
    top_title: str,
    transcript_text: str,
    model: Optional[str] = None,
    system_prompt: Optional[str] = None,
) -> SummarizationResult:
    """
    Generate a summary for a meeting segment (TOP).

    The primary path asks the LLM for structured JSON and renders it into the
    existing editable text summary. Long transcripts are processed via
    map-reduce over transcript chunks. If structured output fails and fallback
    is enabled, a chunked free-text summarization path is used.
    """

    client = _load_openai_client()
    actual_model = model or LLM_MODEL
    actual_system_prompt = build_structured_system_prompt(system_prompt)

    start_time = time.time()
    try:
        structured, chunks_processed = _summarize_structured(
            client,
            top_title=top_title,
            transcript_text=transcript_text,
            model=actual_model,
            system_prompt=actual_system_prompt,
        )
        summary = render_structured_summary(structured)
        if not summary:
            raise StructuredOutputError(
                "Strukturierte Antwort konnte nicht gerendert werden"
            )
        duration_seconds = time.time() - start_time
        return SummarizationResult(
            summary=summary,
            duration_seconds=duration_seconds,
            structured=structured,
            fallback_used=False,
            chunks_processed=chunks_processed,
        )
    except (StructuredOutputError, LLMCallError):
        if not LLM_STRUCTURED_FALLBACK:
            raise

    summary, chunks_processed = _summarize_freetext(
        client,
        top_title=top_title,
        transcript_text=transcript_text,
        model=actual_model,
    )
    duration_seconds = time.time() - start_time
    return SummarizationResult(
        summary=summary,
        duration_seconds=duration_seconds,
        structured=None,
        fallback_used=True,
        chunks_processed=chunks_processed,
    )


def summarize_all_segments(
    tops: list[str],
    segments: dict[int, str],
) -> dict[int, str]:
    """
    Generate summaries for all TOPs.

    Returns:
        Dict mapping TOP index to editable summary text.
    """
    summaries = {}
    for top_idx, transcript_text in segments.items():
        if transcript_text.strip():
            top_title = tops[top_idx] if top_idx < len(tops) else f"TOP {top_idx + 1}"
            summaries[top_idx] = summarize_segment(top_title, transcript_text).summary
    return summaries

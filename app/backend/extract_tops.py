"""
PDF TOP extraction module for German municipal meeting invitations.

Extracts agenda items (Tagesordnungspunkte/TOPs) from PDF invitation documents
using pdfplumber for text extraction and Ollama LLM for intelligent parsing.

Configuration via environment variables:
- LLM_BASE_URL: API endpoint (local default: http://localhost:11434/v1,
  Docker default: http://ollama:11434/v1)
- LLM_MODEL: Model name (default: qwen3:8b)
"""

import logging
import os
import re
import unicodedata
from typing import Optional

from summarize import get_llm_config

logger = logging.getLogger(__name__)

# LLM server configuration (same as summarize.py)
LLM_MODEL = os.environ.get("LLM_MODEL", "qwen3:8b")
LLM_BASE_URL = get_llm_config().base_url
LLM_API_KEY = get_llm_config().api_key
NO_THINK_DIRECTIVE = "/no_think"

# Default system prompt for TOP extraction. Keep this short: reasoning models can
# otherwise spend the whole response budget on hidden reasoning and return no
# message.content through Ollama's OpenAI-compatible endpoint.
DEFAULT_EXTRACTION_PROMPT = """Du bist ein Extraktor. Antworte ohne Denken, ohne Erklärung, nur mit einer nummerierten Liste der Tagesordnungspunkte.
Extrahiere aus der Einladung alle eigentlichen TOPs aus öffentlichem und nichtöffentlichem Teil.
Ignoriere Abschnittsüberschriften wie "TOP I. Öffentlicher Teil" und "TOP II. Nichtöffentlicher Teil" als eigene TOPs.
Ignoriere Bullet-Unterpunkte wie "- Fäkalienentsorgungssatzung - FES".
Entferne Zusatzinfos wie "BE:", "Beschlussvorlage:", "Antrag:" oder "Drucksache:".
Jeder TOP kommt auf eine eigene Zeile im Format: 1. Titel"""


def build_extraction_system_prompt(system_prompt: Optional[str] = None) -> str:
    """Return a prompt that keeps reasoning models from hiding the final answer."""
    prompt = (system_prompt or DEFAULT_EXTRACTION_PROMPT).strip()
    if prompt.startswith(NO_THINK_DIRECTIVE):
        return prompt
    return f"{NO_THINK_DIRECTIVE}\n{prompt}"


def extract_text_from_pdf(pdf_path: str) -> str:
    """
    Extract text content from a PDF file.

    Args:
        pdf_path: Path to the PDF file

    Returns:
        Extracted text as a single string

    Raises:
        RuntimeError: If pdfplumber is not installed or extraction fails
    """
    try:
        import pdfplumber
    except ImportError:
        raise RuntimeError(
            "pdfplumber nicht installiert. Installieren Sie mit: uv add pdfplumber"
        )

    logger.info("Extracting text from uploaded PDF")

    try:
        text_parts = []
        with pdfplumber.open(pdf_path) as pdf:
            for i, page in enumerate(pdf.pages):
                page_text = page.extract_text()
                if page_text:
                    text_parts.append(page_text)
                    logger.debug(f"Page {i + 1}: extracted {len(page_text)} characters")

        full_text = "\n\n".join(text_parts)
        logger.info(f"Total extracted text: {len(full_text)} characters from {len(text_parts)} pages")
        return full_text

    except Exception as e:
        logger.error(
            "Failed to extract text from uploaded PDF (%s)",
            e.__class__.__name__,
        )
        raise RuntimeError(f"PDF-Text konnte nicht extrahiert werden: {str(e)}")


def extract_tops_from_text(
    pdf_text: str,
    model: Optional[str] = None,
    system_prompt: Optional[str] = None,
) -> list[str]:
    """
    Extract TOPs from PDF text using LLM.

    Args:
        pdf_text: Full text extracted from the PDF
        model: LLM model to use (default: from env or qwen3:8b)
        system_prompt: Custom system prompt (default: DEFAULT_EXTRACTION_PROMPT)

    Returns:
        List of TOP titles (including numbering)

    Raises:
        RuntimeError: If OpenAI client is not installed or LLM call fails
    """
    try:
        from openai import OpenAI
    except ImportError:
        raise RuntimeError(
            "OpenAI client nicht installiert. Installieren Sie mit: uv add openai"
        )

    config = get_llm_config(model)
    actual_model = config.model
    actual_system_prompt = build_extraction_system_prompt(system_prompt)

    logger.info(f"Extracting TOPs using model: {actual_model}")

    client = OpenAI(
        base_url=config.base_url,
        api_key=config.api_key,
    )

    user_prompt = f"""Extrahiere alle Tagesordnungspunkte aus diesem Einladungsdokument:

{pdf_text}

TOPs:"""

    try:
        response = client.chat.completions.create(
            model=actual_model,
            messages=[
                {"role": "system", "content": actual_system_prompt},
                {"role": "user", "content": user_prompt},
            ],
            max_tokens=2048,
            temperature=0.1,  # Very low temperature for consistent extraction
        )

        raw_response = response.choices[0].message.content or ""
        logger.debug("LLM TOP extraction returned %s characters", len(raw_response))

        # Parse the response into individual TOPs
        tops = parse_tops_response(raw_response)
        logger.info(f"Extracted {len(tops)} TOPs")

        return tops

    except Exception as e:
        logger.error("LLM TOP extraction failed (%s)", e.__class__.__name__)
        raise RuntimeError(f"TOP-Extraktion fehlgeschlagen: {str(e)}")


def parse_tops_response(response_text: str) -> list[str]:
    """
    Parse the LLM response into a list of TOP titles.

    Handles various numbering formats:
    - "1. Title"
    - "1.1. Title"
    - "I. Title"
    - "II. Title"

    Args:
        response_text: Raw LLM response text

    Returns:
        List of TOP titles (with numbering stripped)
    """
    tops = []
    lines = response_text.strip().split("\n")

    # Regex patterns for different numbering styles
    # Matches: "1.", "1.1.", "1.2.3.", "I.", "II.", etc.
    numbering_pattern = re.compile(
        r"^\s*(?:"
        r"(\d+\.)+|"  # Arabic numerals: 1., 1.1., 1.2.3.
        r"(\d{1,3})\s+|"  # Arabic numerals without punctuation: 01 Title
        r"([IVX]+\.)|"  # Roman numerals: I., II., III., IV.
        r"(\d+\))|"  # Parenthetical: 1), 2)
        r"([a-z]\))"  # Letter: a), b)
        r")\s*"
    )

    for line in lines:
        line = line.strip()
        if not line:
            continue
        if is_agenda_section_heading(line):
            continue

        # Check if line starts with numbering
        match = numbering_pattern.match(line)
        if match:
            # Extract the title after the numbering
            title = line[match.end():].strip()
            if title and not is_agenda_section_heading(title):
                tops.append(title)
        elif line and not line.startswith(("●", "•", "-", "*", "–")):
            # Include non-numbered lines that aren't bullet points
            # (in case LLM returns titles without numbers)
            # But only if they look like titles (not too short, not metadata)
            if (
                len(line) > 5
                and not is_agenda_section_heading(line)
                and not any(
                    skip in line.lower()
                    for skip in ["beschlussvorlage", "antrag:", "drucksache", "seite"]
                )
            ):
                tops.append(line)

    return tops


def is_agenda_section_heading(value: str) -> bool:
    """Return true for agenda section labels, not actual agenda items."""
    normalized = unicodedata.normalize("NFKD", value.lower().replace("ß", "ss"))
    ascii_text = normalized.encode("ascii", "ignore").decode("ascii")
    text = re.sub(r"[^a-z0-9]+", " ", ascii_text).strip()
    text = re.sub(r"^(?:top\s*)?(?:[ivx]+|\d+)\s+", "", text).strip()
    return text in {
        "offentlicher teil",
        "offentliche teil",
        "oeffentlicher teil",
        "oeffentliche teil",
        "nichtoffentlicher teil",
        "nichtoffentliche teil",
        "nichtoeffentlicher teil",
        "nichtoeffentliche teil",
    }


def extract_tops_from_pdf(
    pdf_path: str,
    model: Optional[str] = None,
    system_prompt: Optional[str] = None,
) -> list[str]:
    """
    Extract TOPs from a PDF file (convenience function).

    Combines text extraction and LLM parsing in one call.

    Args:
        pdf_path: Path to the PDF file
        model: LLM model to use (optional)
        system_prompt: Custom system prompt (optional)

    Returns:
        List of TOP titles
    """
    pdf_text = extract_text_from_pdf(pdf_path)
    return extract_tops_from_text(pdf_text, model, system_prompt)

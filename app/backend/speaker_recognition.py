"""Speaker embedding extraction and conservative profile matching."""

from __future__ import annotations

import logging
import math
import os
from dataclasses import dataclass, field
from typing import Any, Iterable

os.environ.setdefault("TORCH_FORCE_NO_WEIGHTS_ONLY_LOAD", "1")

logger = logging.getLogger(__name__)

DEFAULT_EMBEDDING_MODEL = "pyannote/embedding"
DEFAULT_FALLBACK_EMBEDDING_MODELS = ("pyannote/wespeaker-voxceleb-resnet34-LM",)


@dataclass(frozen=True)
class SpeakerEmbeddingConfig:
    model_name: str = DEFAULT_EMBEDDING_MODEL
    enabled: bool = True
    auto_threshold: float = 0.82
    suggest_threshold: float = 0.72
    match_top_k: int = 3
    fallback_model_names: tuple[str, ...] = DEFAULT_FALLBACK_EMBEDDING_MODELS
    min_total_seconds: float = 8.0
    min_segment_seconds: float = 1.5
    max_segment_seconds: float = 12.0
    max_segments_per_speaker: int = 8
    max_profile_embeddings_per_model: int = 16


@dataclass(frozen=True)
class LocalSpeakerEmbedding:
    local_speaker_id: str
    embedding: list[float]
    model_name: str
    quality: float
    reference_embeddings: list[list[float]] = field(default_factory=list)
    quality_metadata: dict[str, Any] = field(default_factory=dict)


@dataclass(frozen=True)
class ProfileReference:
    profile_id: str
    display_name: str
    embedding: list[float]
    embeddings: list[list[float]]
    embedding_count: int


@dataclass(frozen=True)
class SpeakerMatch:
    local_speaker_id: str
    profile_id: str
    display_name: str
    confidence: float
    match_level: str
    status: str = "suggested"
    diagnostics: dict[str, Any] = field(default_factory=dict)


@dataclass(frozen=True)
class SpeakerMatchDiagnostic:
    local_speaker_id: str
    reason_code: str
    reason: str
    best_profile_id: str | None = None
    best_profile_display_name: str | None = None
    best_score: float | None = None
    suggest_threshold: float | None = None
    local_audio_seconds: float | None = None
    local_embedding_available: bool = False
    profile_embedding_count: int = 0


@dataclass(frozen=True)
class SpeakerEmbeddingLoadResult:
    inference: Any | None
    model_name: str | None
    attempted_model_names: tuple[str, ...]
    error: str | None = None


def _get_float_env(name: str, default: float) -> float:
    raw = os.environ.get(name)
    if raw is None:
        return default
    try:
        return float(raw)
    except ValueError:
        logger.warning("Invalid float for %s=%r; using %s", name, raw, default)
        return default


def _get_int_env(name: str, default: int) -> int:
    raw = os.environ.get(name)
    if raw is None:
        return default
    try:
        return int(raw)
    except ValueError:
        logger.warning("Invalid integer for %s=%r; using %s", name, raw, default)
        return default


def _get_bool_env(name: str, default: bool) -> bool:
    raw = os.environ.get(name)
    if raw is None:
        return default
    return raw.strip().lower() not in {"0", "false", "no", "off"}


def _get_csv_env(name: str, default: tuple[str, ...]) -> tuple[str, ...]:
    raw = os.environ.get(name)
    if raw is None:
        return default
    return tuple(item.strip() for item in raw.split(",") if item.strip())


def get_huggingface_token() -> str | None:
    return os.environ.get("HF_TOKEN") or os.environ.get("HUGGINGFACE_TOKEN") or None


def speaker_embedding_config_from_env() -> SpeakerEmbeddingConfig:
    auto_threshold = _get_float_env("SPEAKER_MATCH_AUTO_THRESHOLD", 0.82)
    suggest_threshold = _get_float_env("SPEAKER_MATCH_SUGGEST_THRESHOLD", 0.72)
    if suggest_threshold > auto_threshold:
        logger.warning(
            "SPEAKER_MATCH_SUGGEST_THRESHOLD is above auto threshold; using auto threshold"
        )
        suggest_threshold = auto_threshold

    return SpeakerEmbeddingConfig(
        model_name=os.environ.get("SPEAKER_EMBEDDING_MODEL", DEFAULT_EMBEDDING_MODEL),
        enabled=_get_bool_env("SPEAKER_EMBEDDING_ENABLED", True),
        auto_threshold=auto_threshold,
        suggest_threshold=suggest_threshold,
        match_top_k=max(1, _get_int_env("SPEAKER_MATCH_TOP_K", 3)),
        fallback_model_names=_get_csv_env(
            "SPEAKER_EMBEDDING_FALLBACK_MODELS",
            DEFAULT_FALLBACK_EMBEDDING_MODELS,
        ),
        min_total_seconds=max(
            0.1, _get_float_env("SPEAKER_EMBEDDING_MIN_SECONDS", 8.0)
        ),
        min_segment_seconds=max(
            0.1, _get_float_env("SPEAKER_EMBEDDING_MIN_SEGMENT_SECONDS", 1.5)
        ),
        max_segment_seconds=max(
            0.1, _get_float_env("SPEAKER_EMBEDDING_MAX_SEGMENT_SECONDS", 12.0)
        ),
        max_segments_per_speaker=max(
            1, _get_int_env("SPEAKER_EMBEDDING_MAX_SEGMENTS", 8)
        ),
        max_profile_embeddings_per_model=max(
            1, _get_int_env("SPEAKER_PROFILE_MAX_EMBEDDINGS_PER_MODEL", 16)
        ),
    )


def normalize_embedding(values: Iterable[Any]) -> list[float] | None:
    vector = [float(value) for value in values]
    norm = math.sqrt(sum(value * value for value in vector))
    if not vector or norm <= 0:
        return None
    return [value / norm for value in vector]


def mean_normalized_embedding(embeddings: Iterable[Iterable[Any]]) -> list[float] | None:
    normalized = [
        embedding
        for values in embeddings
        if (embedding := normalize_embedding(values)) is not None
    ]
    if not normalized:
        return None

    dimensions = len(normalized[0])
    compatible = [
        embedding for embedding in normalized if len(embedding) == dimensions
    ]
    if not compatible:
        return None

    mean = [
        sum(embedding[index] for embedding in compatible) / len(compatible)
        for index in range(dimensions)
    ]
    return normalize_embedding(mean)


def cosine_similarity(left: Iterable[Any], right: Iterable[Any]) -> float | None:
    left_normalized = normalize_embedding(left)
    right_normalized = normalize_embedding(right)
    if left_normalized is None or right_normalized is None:
        return None
    if len(left_normalized) != len(right_normalized):
        return None
    return sum(a * b for a, b in zip(left_normalized, right_normalized))


def build_profile_references(
    profiles: Iterable[dict[str, Any]],
    embeddings_by_profile: dict[str, list[dict[str, Any]]],
) -> list[ProfileReference]:
    references: list[ProfileReference] = []
    for profile in profiles:
        profile_id = str(profile["profile_id"])
        embeddings = []
        for item in embeddings_by_profile.get(profile_id, []):
            embedding = normalize_embedding(item.get("embedding") or [])
            if embedding is not None:
                embeddings.append(embedding)
        reference_embedding = mean_normalized_embedding(embeddings)
        if reference_embedding is None:
            continue
        references.append(
            ProfileReference(
                profile_id=profile_id,
                display_name=str(profile["display_name"]),
                embedding=reference_embedding,
                embeddings=embeddings,
                embedding_count=len(embeddings),
            )
        )
    return references


def profile_reference_similarity(
    local_embedding: Iterable[Any],
    reference: ProfileReference,
    *,
    top_k: int = 3,
) -> tuple[float, dict[str, Any]] | None:
    local_normalized = normalize_embedding(local_embedding)
    if local_normalized is None:
        return None

    scores = [
        score
        for embedding in reference.embeddings
        if (score := cosine_similarity(local_normalized, embedding)) is not None
    ]
    mean_score = cosine_similarity(local_normalized, reference.embedding)
    if not scores and mean_score is None:
        return None

    sorted_scores = sorted(scores, reverse=True)
    best_score = sorted_scores[0] if sorted_scores else mean_score
    selected_top_k = sorted_scores[: max(1, top_k)]
    top_k_score = (
        sum(selected_top_k) / len(selected_top_k)
        if selected_top_k
        else best_score
    )
    mean_component = mean_score if mean_score is not None else best_score
    combined_score = (
        0.6 * float(best_score)
        + 0.25 * float(top_k_score)
        + 0.15 * float(mean_component)
    )
    score = max(float(best_score), combined_score)
    return score, {
        "best_score": round(float(best_score), 4),
        "top_k_score": round(float(top_k_score), 4),
        "mean_score": round(float(mean_component), 4),
        "embedding_count": reference.embedding_count,
        "top_k": min(len(selected_top_k), max(1, top_k)),
    }


def match_speaker_embeddings(
    local_embeddings: Iterable[LocalSpeakerEmbedding],
    profile_references: Iterable[ProfileReference],
    *,
    auto_threshold: float,
    suggest_threshold: float,
    top_k: int = 3,
) -> list[SpeakerMatch]:
    """Return one reviewable profile suggestion per local speaker, if confident."""
    references = list(profile_references)
    matches: list[SpeakerMatch] = []

    for local in local_embeddings:
        best: tuple[ProfileReference, float, dict[str, Any]] | None = None
        for reference in references:
            result = profile_reference_similarity(
                local.embedding,
                reference,
                top_k=top_k,
            )
            if result is None:
                continue
            score, diagnostics = result
            if best is None or score > best[1]:
                best = (reference, score, diagnostics)

        if best is None:
            continue

        reference, score, diagnostics = best
        if score < suggest_threshold:
            continue

        matches.append(
            SpeakerMatch(
                local_speaker_id=local.local_speaker_id,
                profile_id=reference.profile_id,
                display_name=reference.display_name,
                confidence=round(max(0.0, min(1.0, score)), 4),
                match_level="auto" if score >= auto_threshold else "suggest",
                diagnostics=diagnostics,
            )
        )

    return sorted(matches, key=lambda item: item.confidence, reverse=True)


def diagnose_speaker_matches(
    *,
    local_speaker_ids: Iterable[str],
    local_embeddings: Iterable[LocalSpeakerEmbedding],
    profile_references: Iterable[ProfileReference],
    config: SpeakerEmbeddingConfig,
    local_audio_seconds: dict[str, float] | None = None,
    embedding_model_available: bool = True,
) -> list[SpeakerMatchDiagnostic]:
    references = list(profile_references)
    embeddings_by_speaker = {
        embedding.local_speaker_id: embedding for embedding in local_embeddings
    }
    profile_embedding_count = sum(reference.embedding_count for reference in references)
    diagnostics: list[SpeakerMatchDiagnostic] = []

    for local_speaker_id in sorted({str(item) for item in local_speaker_ids}):
        audio_seconds = (local_audio_seconds or {}).get(local_speaker_id)
        local_embedding = embeddings_by_speaker.get(local_speaker_id)

        if not config.enabled:
            diagnostics.append(
                SpeakerMatchDiagnostic(
                    local_speaker_id=local_speaker_id,
                    reason_code="embedding_disabled",
                    reason="Embedding-Modell ist deaktiviert",
                    suggest_threshold=config.suggest_threshold,
                    local_audio_seconds=audio_seconds,
                    profile_embedding_count=profile_embedding_count,
                )
            )
            continue

        if local_embedding is None:
            if not embedding_model_available:
                reason_code = "embedding_model_unavailable"
                reason = "Embedding-Modell nicht verfügbar"
            else:
                reason_code = "insufficient_speaker_audio"
                reason = "zu wenig qualifiziertes Sprecher-Audio"
            diagnostics.append(
                SpeakerMatchDiagnostic(
                    local_speaker_id=local_speaker_id,
                    reason_code=reason_code,
                    reason=reason,
                    suggest_threshold=config.suggest_threshold,
                    local_audio_seconds=audio_seconds,
                    profile_embedding_count=profile_embedding_count,
                )
            )
            continue

        if not references:
            diagnostics.append(
                SpeakerMatchDiagnostic(
                    local_speaker_id=local_speaker_id,
                    reason_code="no_profile_embedding",
                    reason="kein Profil-Embedding",
                    suggest_threshold=config.suggest_threshold,
                    local_audio_seconds=audio_seconds,
                    local_embedding_available=True,
                    profile_embedding_count=0,
                )
            )
            continue

        best: tuple[ProfileReference, float] | None = None
        for reference in references:
            result = profile_reference_similarity(
                local_embedding.embedding,
                reference,
                top_k=config.match_top_k,
            )
            if result is None:
                continue
            score, _ = result
            if best is None or score > best[1]:
                best = (reference, score)

        if best is None:
            diagnostics.append(
                SpeakerMatchDiagnostic(
                    local_speaker_id=local_speaker_id,
                    reason_code="no_compatible_profile_embedding",
                    reason="kein kompatibles Profil-Embedding",
                    suggest_threshold=config.suggest_threshold,
                    local_audio_seconds=audio_seconds,
                    local_embedding_available=True,
                    profile_embedding_count=profile_embedding_count,
                )
            )
            continue

        reference, score = best
        if score < config.suggest_threshold:
            diagnostics.append(
                SpeakerMatchDiagnostic(
                    local_speaker_id=local_speaker_id,
                    reason_code="below_threshold",
                    reason="unter Schwellwert",
                    best_profile_id=reference.profile_id,
                    best_profile_display_name=reference.display_name,
                    best_score=round(max(0.0, min(1.0, score)), 4),
                    suggest_threshold=config.suggest_threshold,
                    local_audio_seconds=audio_seconds,
                    local_embedding_available=True,
                    profile_embedding_count=profile_embedding_count,
                )
            )

    return diagnostics


def load_pyannote_embedding_inference(
    *,
    device: str,
    config: SpeakerEmbeddingConfig | None = None,
) -> Any | None:
    """Load PyAnnote's speaker embedding model if available."""
    return load_pyannote_embedding_inference_result(
        device=device,
        config=config,
    ).inference


def load_pyannote_embedding_inference_result(
    *,
    device: str,
    config: SpeakerEmbeddingConfig | None = None,
) -> SpeakerEmbeddingLoadResult:
    """Load PyAnnote speaker embeddings with fallback models and diagnostics."""
    config = config or speaker_embedding_config_from_env()
    if not config.enabled:
        logger.info("Speaker embedding extraction disabled")
        return SpeakerEmbeddingLoadResult(
            inference=None,
            model_name=None,
            attempted_model_names=(),
            error="speaker embedding extraction disabled",
        )

    try:
        from pyannote.audio import Inference, Model
        import torch
    except Exception as exc:
        logger.warning("PyAnnote embedding inference is unavailable: %s", exc)
        return SpeakerEmbeddingLoadResult(
            inference=None,
            model_name=None,
            attempted_model_names=(),
            error=f"PyAnnote embedding inference is unavailable: {exc}",
        )

    hf_token = get_huggingface_token()
    torch_device = torch.device(device)
    attempted = tuple(
        dict.fromkeys((config.model_name, *config.fallback_model_names))
    )
    errors: list[str] = []

    for model_name in attempted:
        try:
            try:
                model = Model.from_pretrained(model_name, token=hf_token)
            except TypeError:
                try:
                    model = Model.from_pretrained(model_name, use_auth_token=hf_token)
                except TypeError:
                    model = Model.from_pretrained(model_name)
            if model is None:
                raise RuntimeError(
                    "model could not be downloaded or access is not granted"
                )
            inference = Inference(
                model,
                window="whole",
                device=torch_device,
            )
            setattr(inference, "_speaker_embedding_model_name", model_name)
            logger.info("Speaker embedding model loaded: %s", model_name)
            return SpeakerEmbeddingLoadResult(
                inference=inference,
                model_name=model_name,
                attempted_model_names=attempted,
                error=None,
            )
        except Exception as exc:
            message = f"{model_name}: {exc}"
            errors.append(message)
            logger.warning("Failed to load speaker embedding model %s: %s", model_name, exc)

    return SpeakerEmbeddingLoadResult(
        inference=None,
        model_name=None,
        attempted_model_names=attempted,
        error="; ".join(errors) if errors else "no speaker embedding model configured",
    )


def _segment_duration(segment: Any) -> float:
    return max(0.0, float(segment.end) - float(segment.start))


def _overlaps_other_speaker(
    segment: Any,
    speaker: str,
    intervals: list[tuple[float, float, str]],
) -> bool:
    start = float(segment.start)
    end = float(segment.end)
    for other_start, other_end, other_speaker in intervals:
        if other_speaker == speaker:
            continue
        if min(end, other_end) - max(start, other_start) > 0.05:
            return True
    return False


def _extract_vector(value: Any) -> list[float] | None:
    data = getattr(value, "data", value)
    try:
        import numpy as np
        import torch

        if isinstance(data, torch.Tensor):
            data = data.detach().cpu().numpy()
        array = np.asarray(data, dtype=float)
        if array.ndim == 0:
            return None
        if array.ndim > 1:
            array = array.reshape(-1, array.shape[-1]).mean(axis=0)
        return [float(item) for item in array.tolist()]
    except Exception as exc:
        logger.warning("Could not convert speaker embedding output: %s", exc)
        return None


def _row_value(row: Any, *names: str) -> Any:
    for name in names:
        try:
            value = row.get(name)  # pandas Series and dict-like rows
        except Exception:
            value = None
        if value is not None:
            return value
        try:
            value = row[name]
        except Exception:
            value = None
        if value is not None:
            return value
        value = getattr(row, name, None)
        if value is not None:
            return value
    return None


def _coerce_diarization_segment(row: Any) -> tuple[Any, str] | None:
    segment = _row_value(row, "segment")
    speaker = _row_value(row, "speaker", "label")

    if segment is not None:
        start = getattr(segment, "start", None)
        end = getattr(segment, "end", None)
    else:
        start = _row_value(row, "start")
        end = _row_value(row, "end", "stop")

    if start is None or end is None or speaker is None:
        return None

    try:
        start_float = float(start)
        end_float = float(end)
    except (TypeError, ValueError):
        return None

    if end_float <= start_float:
        return None

    return (
        type("DiarizationSegment", (), {"start": start_float, "end": end_float})(),
        str(speaker),
    )


def iter_diarization_speaker_segments(diarize_segments: Any) -> list[tuple[Any, str]]:
    """Return (segment, speaker) pairs from pyannote annotations or WhisperX frames."""
    if diarize_segments is None:
        return []

    if hasattr(diarize_segments, "itertracks"):
        try:
            return [
                (segment, str(label))
                for segment, _, label in diarize_segments.itertracks(yield_label=True)
            ]
        except Exception as exc:
            logger.warning("Could not iterate pyannote diarization segments: %s", exc)
            return []

    rows: list[Any] = []
    if hasattr(diarize_segments, "iterrows"):
        try:
            rows = [row for _, row in diarize_segments.iterrows()]
        except Exception as exc:
            logger.warning("Could not iterate dataframe diarization segments: %s", exc)
            return []
    elif isinstance(diarize_segments, dict):
        rows = [diarize_segments]
    else:
        try:
            rows = list(diarize_segments)
        except TypeError:
            rows = []

    pairs = []
    for row in rows:
        coerced = _coerce_diarization_segment(row)
        if coerced is not None:
            pairs.append(coerced)
    return pairs


def extract_local_speaker_embeddings(
    *,
    audio: Any,
    diarize_segments: Any,
    embedding_inference: Any | None,
    config: SpeakerEmbeddingConfig | None = None,
) -> list[LocalSpeakerEmbedding]:
    """Extract one normalized embedding per local diarization speaker."""
    config = config or speaker_embedding_config_from_env()
    if embedding_inference is None or not config.enabled:
        return []
    model_name = str(
        getattr(embedding_inference, "_speaker_embedding_model_name", config.model_name)
    )

    try:
        import torch
        from pyannote.core import Segment
    except Exception as exc:
        logger.warning("Speaker embedding dependencies unavailable: %s", exc)
        return []

    try:
        waveform = torch.as_tensor(audio, dtype=torch.float32)
        if waveform.ndim == 1:
            waveform = waveform.unsqueeze(0)
        audio_file = {"waveform": waveform, "sample_rate": 16000}
    except Exception as exc:
        logger.warning("Could not prepare audio for speaker embedding: %s", exc)
        return []

    intervals: list[tuple[float, float, str]] = []
    candidates_by_speaker: dict[str, list[Any]] = {}
    excluded_short_by_speaker: dict[str, int] = {}
    excluded_overlap_by_speaker: dict[str, int] = {}

    raw_segments = iter_diarization_speaker_segments(diarize_segments)
    if not raw_segments:
        logger.warning("No diarization segments available for speaker embeddings")
        return []
    for segment, label in raw_segments:
        intervals.append((float(segment.start), float(segment.end), label))

    for segment, label in raw_segments:
        duration = _segment_duration(segment)
        if duration < config.min_segment_seconds:
            excluded_short_by_speaker[label] = excluded_short_by_speaker.get(label, 0) + 1
            continue
        if _overlaps_other_speaker(segment, label, intervals):
            excluded_overlap_by_speaker[label] = (
                excluded_overlap_by_speaker.get(label, 0) + 1
            )
            continue
        candidates_by_speaker.setdefault(label, []).append(segment)

    results: list[LocalSpeakerEmbedding] = []
    for speaker, candidates in candidates_by_speaker.items():
        candidates = sorted(candidates, key=lambda item: _segment_duration(item), reverse=True)
        embeddings: list[list[float]] = []
        reference_embeddings: list[list[float]] = []
        selected_segments: list[dict[str, float]] = []
        total_seconds = 0.0

        for segment in candidates:
            if len(selected_segments) >= config.max_segments_per_speaker:
                break
            start = float(segment.start)
            end = min(float(segment.end), start + config.max_segment_seconds)
            if end - start < config.min_segment_seconds:
                continue
            try:
                output = embedding_inference.crop(audio_file, Segment(start, end))
            except Exception as exc:
                logger.warning(
                    "Failed to extract embedding for %s %.2f-%.2f: %s",
                    speaker,
                    start,
                    end,
                    exc,
                )
                continue
            vector = _extract_vector(output)
            normalized = normalize_embedding(vector or [])
            if normalized is None:
                continue
            embeddings.append(normalized)
            reference_embeddings.append(normalized)
            selected_segments.append(
                {"start": round(start, 3), "end": round(end, 3), "duration": round(end - start, 3)}
            )
            total_seconds += end - start

        speaker_embedding = mean_normalized_embedding(embeddings)
        if speaker_embedding is None or total_seconds < config.min_total_seconds:
            continue

        quality = min(1.0, total_seconds / config.min_total_seconds) * min(
            1.0, len(selected_segments) / 3
        )
        results.append(
            LocalSpeakerEmbedding(
                local_speaker_id=speaker,
                embedding=speaker_embedding,
                model_name=model_name,
                quality=round(quality, 4),
                reference_embeddings=reference_embeddings,
                quality_metadata={
                    "selected_segments": selected_segments,
                    "selected_segment_count": len(selected_segments),
                    "candidate_segment_count": len(candidates),
                    "total_seconds": round(total_seconds, 3),
                    "min_total_seconds": config.min_total_seconds,
                    "min_segment_seconds": config.min_segment_seconds,
                    "excluded_short_count": excluded_short_by_speaker.get(speaker, 0),
                    "excluded_overlap_count": excluded_overlap_by_speaker.get(speaker, 0),
                    "model_name": model_name,
                },
            )
        )

    return sorted(results, key=lambda item: item.local_speaker_id)

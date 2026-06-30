"""Speaker embedding extraction and conservative profile matching."""

from __future__ import annotations

import logging
import math
import os
from dataclasses import dataclass, field
from typing import Any, Iterable


logger = logging.getLogger(__name__)

DEFAULT_EMBEDDING_MODEL = "pyannote/embedding"


@dataclass(frozen=True)
class SpeakerEmbeddingConfig:
    model_name: str = DEFAULT_EMBEDDING_MODEL
    enabled: bool = True
    auto_threshold: float = 0.82
    suggest_threshold: float = 0.72
    min_total_seconds: float = 8.0
    min_segment_seconds: float = 1.5
    max_segment_seconds: float = 12.0
    max_segments_per_speaker: int = 8


@dataclass(frozen=True)
class LocalSpeakerEmbedding:
    local_speaker_id: str
    embedding: list[float]
    model_name: str
    quality: float
    quality_metadata: dict[str, Any] = field(default_factory=dict)


@dataclass(frozen=True)
class ProfileReference:
    profile_id: str
    display_name: str
    embedding: list[float]
    embedding_count: int


@dataclass(frozen=True)
class SpeakerMatch:
    local_speaker_id: str
    profile_id: str
    display_name: str
    confidence: float
    match_level: str
    status: str = "suggested"


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
        embeddings = [
            item.get("embedding")
            for item in embeddings_by_profile.get(profile_id, [])
            if item.get("embedding") is not None
        ]
        reference_embedding = mean_normalized_embedding(embeddings)
        if reference_embedding is None:
            continue
        references.append(
            ProfileReference(
                profile_id=profile_id,
                display_name=str(profile["display_name"]),
                embedding=reference_embedding,
                embedding_count=len(embeddings),
            )
        )
    return references


def match_speaker_embeddings(
    local_embeddings: Iterable[LocalSpeakerEmbedding],
    profile_references: Iterable[ProfileReference],
    *,
    auto_threshold: float,
    suggest_threshold: float,
) -> list[SpeakerMatch]:
    """Return one reviewable profile suggestion per local speaker, if confident."""
    references = list(profile_references)
    matches: list[SpeakerMatch] = []

    for local in local_embeddings:
        best: tuple[ProfileReference, float] | None = None
        for reference in references:
            score = cosine_similarity(local.embedding, reference.embedding)
            if score is None:
                continue
            if best is None or score > best[1]:
                best = (reference, score)

        if best is None:
            continue

        reference, score = best
        if score < suggest_threshold:
            continue

        matches.append(
            SpeakerMatch(
                local_speaker_id=local.local_speaker_id,
                profile_id=reference.profile_id,
                display_name=reference.display_name,
                confidence=round(max(0.0, min(1.0, score)), 4),
                match_level="auto" if score >= auto_threshold else "suggest",
            )
        )

    return sorted(matches, key=lambda item: item.confidence, reverse=True)


def load_pyannote_embedding_inference(
    *,
    device: str,
    config: SpeakerEmbeddingConfig | None = None,
) -> Any | None:
    """Load PyAnnote's speaker embedding model if available."""
    config = config or speaker_embedding_config_from_env()
    if not config.enabled:
        logger.info("Speaker embedding extraction disabled")
        return None

    try:
        from pyannote.audio import Inference
        import torch
    except Exception as exc:
        logger.warning("PyAnnote embedding inference is unavailable: %s", exc)
        return None

    try:
        hf_token = os.environ.get("HF_TOKEN") or None
        torch_device = torch.device(device)
        inference = Inference(
            config.model_name,
            window="whole",
            device=torch_device,
            use_auth_token=hf_token,
        )
        logger.info("Speaker embedding model loaded: %s", config.model_name)
        return inference
    except TypeError:
        try:
            inference = Inference(
                config.model_name,
                window="whole",
                device=torch.device(device),
            )
            logger.info("Speaker embedding model loaded: %s", config.model_name)
            return inference
        except Exception as exc:
            logger.warning("Failed to load speaker embedding model: %s", exc)
            return None
    except Exception as exc:
        logger.warning("Failed to load speaker embedding model: %s", exc)
        return None


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

    try:
        iterator = diarize_segments.itertracks(yield_label=True)
    except Exception as exc:
        logger.warning("Could not iterate diarization segments for embeddings: %s", exc)
        return []

    raw_segments = [(segment, str(label)) for segment, _, label in iterator]
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
            selected_segments.append(
                {"start": round(start, 3), "end": round(end, 3), "duration": round(end - start, 3)}
            )
            total_seconds += end - start
            if total_seconds >= config.min_total_seconds:
                break

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
                model_name=config.model_name,
                quality=round(quality, 4),
                quality_metadata={
                    "selected_segments": selected_segments,
                    "selected_segment_count": len(selected_segments),
                    "candidate_segment_count": len(candidates),
                    "total_seconds": round(total_seconds, 3),
                    "min_total_seconds": config.min_total_seconds,
                    "min_segment_seconds": config.min_segment_seconds,
                    "excluded_short_count": excluded_short_by_speaker.get(speaker, 0),
                    "excluded_overlap_count": excluded_overlap_by_speaker.get(speaker, 0),
                    "model_name": config.model_name,
                },
            )
        )

    return sorted(results, key=lambda item: item.local_speaker_id)

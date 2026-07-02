import sys
import types

import pytest

from speaker_recognition import (
    LocalSpeakerEmbedding,
    build_profile_references,
    cosine_similarity,
    diagnose_speaker_matches,
    match_speaker_embeddings,
    mean_normalized_embedding,
    normalize_embedding,
    SpeakerEmbeddingConfig,
    extract_local_speaker_embeddings,
    load_pyannote_embedding_inference_result,
)


def test_normalize_and_cosine_similarity_are_dimension_safe():
    assert normalize_embedding([3, 4]) == [0.6, 0.8]
    assert normalize_embedding([0, 0]) is None
    assert cosine_similarity([1, 0], [0, 1]) == pytest.approx(0.0)
    assert cosine_similarity([1, 0], [1, 0]) == pytest.approx(1.0)
    assert cosine_similarity([1, 0], [1, 0, 0]) is None


def test_profile_references_average_normalized_embeddings():
    references = build_profile_references(
        [{"profile_id": "alice", "display_name": "Alice"}],
        {
            "alice": [
                {"embedding": [1, 0]},
                {"embedding": [0.8, 0.2]},
                {"embedding": [0, 0]},
            ]
        },
    )

    assert len(references) == 1
    assert references[0].profile_id == "alice"
    assert len(references[0].embeddings) == 2
    assert references[0].embedding == pytest.approx(
        mean_normalized_embedding([[1, 0], [0.8, 0.2]])
    )


def test_matching_returns_reviewable_suggestions_only_above_thresholds():
    references = build_profile_references(
        [
            {"profile_id": "alice", "display_name": "Alice"},
            {"profile_id": "bob", "display_name": "Bob"},
        ],
        {
            "alice": [{"embedding": [1, 0]}],
            "bob": [{"embedding": [0, 1]}],
        },
    )
    local_embeddings = [
        LocalSpeakerEmbedding(
            local_speaker_id="SPEAKER_00",
            embedding=[0.95, 0.05],
            model_name="test",
            quality=1.0,
        ),
        LocalSpeakerEmbedding(
            local_speaker_id="SPEAKER_01",
            embedding=[0.55, 0.45],
            model_name="test",
            quality=1.0,
        ),
        LocalSpeakerEmbedding(
            local_speaker_id="SPEAKER_02",
            embedding=[0.2, 0.8],
            model_name="test",
            quality=1.0,
        ),
    ]

    matches = match_speaker_embeddings(
        local_embeddings,
        references,
        auto_threshold=0.98,
        suggest_threshold=0.9,
        top_k=2,
    )

    assert [(match.local_speaker_id, match.profile_id) for match in matches] == [
        ("SPEAKER_00", "alice"),
        ("SPEAKER_02", "bob"),
    ]
    assert {match.status for match in matches} == {"suggested"}
    assert {match.match_level for match in matches} == {"auto", "suggest"}
    assert all(match.diagnostics["embedding_count"] == 1 for match in matches)


def test_matching_uses_multiple_profile_references_best_of():
    references = build_profile_references(
        [{"profile_id": "alice", "display_name": "Alice"}],
        {
            "alice": [
                {"embedding": [1, 0]},
                {"embedding": [0, 1]},
            ]
        },
    )

    matches = match_speaker_embeddings(
        [
            LocalSpeakerEmbedding(
                local_speaker_id="SPEAKER_00",
                embedding=[1, 0],
                model_name="test",
                quality=1.0,
            )
        ],
        references,
        auto_threshold=0.95,
        suggest_threshold=0.9,
        top_k=2,
    )

    assert len(matches) == 1
    assert matches[0].profile_id == "alice"
    assert matches[0].confidence == pytest.approx(1.0)
    assert matches[0].diagnostics["best_score"] == pytest.approx(1.0)


def test_matching_does_not_suggest_low_scores():
    references = build_profile_references(
        [{"profile_id": "alice", "display_name": "Alice"}],
        {"alice": [{"embedding": [1, 0]}]},
    )

    matches = match_speaker_embeddings(
        [
            LocalSpeakerEmbedding(
                local_speaker_id="SPEAKER_00",
                embedding=[0, 1],
                model_name="test",
                quality=1.0,
            )
        ],
        references,
        auto_threshold=0.9,
        suggest_threshold=0.75,
    )

    assert matches == []


def test_matching_without_profiles_returns_no_suggestions():
    matches = match_speaker_embeddings(
        [
            LocalSpeakerEmbedding(
                local_speaker_id="SPEAKER_00",
                embedding=[1, 0],
                model_name="test",
                quality=1.0,
            )
        ],
        [],
        auto_threshold=0.9,
        suggest_threshold=0.75,
    )

    assert matches == []


def test_match_diagnostics_explain_missing_suggestions():
    config = SpeakerEmbeddingConfig(
        enabled=True,
        suggest_threshold=0.75,
        min_total_seconds=8.0,
    )
    references = build_profile_references(
        [{"profile_id": "alice", "display_name": "Alice"}],
        {"alice": [{"embedding": [1, 0]}]},
    )

    diagnostics = diagnose_speaker_matches(
        local_speaker_ids=["SPEAKER_00", "SPEAKER_01"],
        local_embeddings=[
            LocalSpeakerEmbedding(
                local_speaker_id="SPEAKER_00",
                embedding=[0, 1],
                model_name="test",
                quality=1.0,
            )
        ],
        profile_references=references,
        config=config,
        local_audio_seconds={"SPEAKER_00": 12.0, "SPEAKER_01": 2.0},
    )

    by_speaker = {item.local_speaker_id: item for item in diagnostics}
    assert by_speaker["SPEAKER_00"].reason_code == "below_threshold"
    assert by_speaker["SPEAKER_00"].best_profile_id == "alice"
    assert by_speaker["SPEAKER_01"].reason_code == "insufficient_speaker_audio"


def test_extract_local_embeddings_keeps_multiple_quality_filtered_references(
    monkeypatch,
):
    class FakeTensor:
        ndim = 1

        def unsqueeze(self, _dimension):
            self.ndim = 2
            return self

    fake_torch = types.SimpleNamespace(
        Tensor=type("Tensor", (), {}),
        float32="float32",
        as_tensor=lambda _audio, dtype=None: FakeTensor(),
    )

    class FakeSegment:
        def __init__(self, start, end):
            self.start = start
            self.end = end

    fake_pyannote_core = types.SimpleNamespace(Segment=FakeSegment)
    monkeypatch.setitem(sys.modules, "torch", fake_torch)
    monkeypatch.setitem(sys.modules, "pyannote.core", fake_pyannote_core)

    class FakeDiarization:
        def itertracks(self, yield_label=False):
            assert yield_label is True
            return iter(
                [
                    (FakeSegment(0.0, 2.0), None, "SPEAKER_00"),
                    (FakeSegment(2.0, 4.0), None, "SPEAKER_00"),
                    (FakeSegment(4.0, 6.0), None, "SPEAKER_00"),
                    (FakeSegment(6.0, 6.4), None, "SPEAKER_00"),
                    (FakeSegment(8.0, 10.0), None, "SPEAKER_00"),
                    (FakeSegment(8.5, 9.5), None, "SPEAKER_01"),
                ]
            )

    class FakeInference:
        def __init__(self):
            self.calls = []

        def crop(self, _audio_file, segment):
            self.calls.append((segment.start, segment.end))
            return [1.0, segment.start + 1.0]

    inference = FakeInference()

    embeddings = extract_local_speaker_embeddings(
        audio=[0.0, 0.0],
        diarize_segments=FakeDiarization(),
        embedding_inference=inference,
        config=SpeakerEmbeddingConfig(
            model_name="test-model",
            min_total_seconds=4.0,
            min_segment_seconds=1.0,
            max_segment_seconds=3.0,
            max_segments_per_speaker=3,
        ),
    )

    assert len(embeddings) == 1
    assert embeddings[0].local_speaker_id == "SPEAKER_00"
    assert len(embeddings[0].reference_embeddings) == 3
    assert embeddings[0].quality_metadata["excluded_short_count"] == 1
    assert embeddings[0].quality_metadata["excluded_overlap_count"] == 1
    assert inference.calls == [(0.0, 2.0), (2.0, 4.0), (4.0, 6.0)]


def test_embedding_loader_uses_fallback_when_primary_model_is_unavailable(
    monkeypatch,
):
    class FakeModel:
        @staticmethod
        def from_pretrained(model_name, use_auth_token=None):
            if model_name == "primary":
                return None
            return {"model_name": model_name}

    class FakeInference:
        def __init__(self, model, window, device):
            self.model = model
            self.window = window
            self.device = device

    fake_pyannote_audio = types.SimpleNamespace(
        Model=FakeModel,
        Inference=FakeInference,
    )
    fake_torch = types.SimpleNamespace(
        Tensor=type("Tensor", (), {}),
        device=lambda value: f"device:{value}",
    )
    monkeypatch.setitem(sys.modules, "pyannote.audio", fake_pyannote_audio)
    monkeypatch.setitem(sys.modules, "torch", fake_torch)

    result = load_pyannote_embedding_inference_result(
        device="cpu",
        config=SpeakerEmbeddingConfig(
            model_name="primary",
            fallback_model_names=("fallback",),
        ),
    )

    assert result.inference is not None
    assert result.model_name == "fallback"
    assert result.attempted_model_names == ("primary", "fallback")
    assert getattr(result.inference, "_speaker_embedding_model_name") == "fallback"

import pytest

from speaker_recognition import (
    LocalSpeakerEmbedding,
    build_profile_references,
    cosine_similarity,
    match_speaker_embeddings,
    mean_normalized_embedding,
    normalize_embedding,
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
    )

    assert [(match.local_speaker_id, match.profile_id) for match in matches] == [
        ("SPEAKER_00", "alice"),
        ("SPEAKER_02", "bob"),
    ]
    assert {match.status for match in matches} == {"suggested"}
    assert {match.match_level for match in matches} == {"auto", "suggest"}


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

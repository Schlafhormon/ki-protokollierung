import pytest

from extract_tops import (
    build_extraction_system_prompt,
    extract_tops_from_text,
    parse_tops_response,
)


@pytest.mark.parametrize(
    ("response_text", "expected"),
    [
        (
            "1. Begruessung\n2. Haushalt 2026\n2.1. Investitionen",
            ["Begruessung", "Haushalt 2026", "Investitionen"],
        ),
        (
            "I. Oeffentlicher Teil\nII. Nichtoeffentlicher Teil\n1) Verschiedenes",
            ["Oeffentlicher Teil", "Nichtoeffentlicher Teil", "Verschiedenes"],
        ),
        (
            "Beschlussvorlage: 123/2026\nUnnummerierter TOP\n- nur ein Unterpunkt",
            ["Unnummerierter TOP"],
        ),
    ],
)
def test_parse_tops_response_handles_common_llm_formats(response_text, expected):
    assert parse_tops_response(response_text) == expected


def test_extract_tops_from_text_uses_openai_client_and_parses_response(fake_openai_module):
    fake_openai_module.content = "1. Genehmigung der Niederschrift\n2. Haushalt"

    tops = extract_tops_from_text(
        "Einladung zur Sitzung",
        model="test-model",
        system_prompt="Nur TOPs extrahieren",
    )

    assert tops == ["Genehmigung der Niederschrift", "Haushalt"]

    client = fake_openai_module.instances[0]
    assert client.kwargs["base_url"]
    request = client.calls[0]
    assert request["model"] == "test-model"
    assert request["temperature"] == 0.1
    assert request["messages"][0] == {
        "role": "system",
        "content": "/no_think\nNur TOPs extrahieren",
    }
    assert "Einladung zur Sitzung" in request["messages"][1]["content"]


def test_build_extraction_system_prompt_does_not_duplicate_no_think():
    assert build_extraction_system_prompt("/no_think\nNur TOPs") == "/no_think\nNur TOPs"

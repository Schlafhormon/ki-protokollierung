import summarize


def structured_response(**overrides):
    payload = {
        "discussion": ["Die Vorsitzende erlaeuterte den Sachverhalt."],
        "decisions": [],
        "votes": [],
        "action_items": [],
        "open_points": [],
        "uncertainties": [],
    }
    payload.update(overrides)
    import json

    return json.dumps(payload)


def test_summarize_segment_uses_structured_output_and_returns_duration(
    fake_openai_module,
    monkeypatch,
):
    fake_openai_module.responses = [
        structured_response(
            decisions=["Der Ausschuss empfahl die Annahme der Vorlage."],
            votes=["Die Empfehlung erfolgte einstimmig."],
        )
    ]
    times = iter([100.0, 102.5])
    monkeypatch.setattr(summarize.time, "time", lambda: next(times))

    result = summarize.summarize_segment(
        "Haushalt",
        "SPEAKER_00: Wir beraten den Haushalt.",
        model="test-model",
        system_prompt="Formal als JSON zusammenfassen",
    )

    assert "Diskussion:" in result.summary
    assert "Die Vorsitzende erlaeuterte den Sachverhalt." in result.summary
    assert "Beschluss:" in result.summary
    assert "Abstimmung:" in result.summary
    assert result.duration_seconds == 2.5
    assert result.structured is not None
    assert result.structured.decisions == [
        "Der Ausschuss empfahl die Annahme der Vorlage."
    ]
    assert result.fallback_used is False
    assert result.chunks_processed == 1

    client = fake_openai_module.instances[0]
    assert client.kwargs["base_url"]
    assert client.kwargs["timeout"] == summarize.LLM_TIMEOUT_SECONDS
    request = client.calls[0]
    assert request["model"] == "test-model"
    assert request["temperature"] == 0.2
    assert request["max_tokens"] == 1400
    assert request["messages"][0]["role"] == "system"
    assert "Gib ausschließlich valides JSON" in request["messages"][0]["content"]
    assert "Formal als JSON zusammenfassen" in request["messages"][0]["content"]
    assert "TOP: Haushalt" in request["messages"][1]["content"]
    assert "SPEAKER_00: Wir beraten den Haushalt." in request["messages"][1]["content"]


def test_summarize_segment_uses_map_reduce_for_long_transcripts(
    fake_openai_module,
    monkeypatch,
):
    monkeypatch.setattr(summarize, "LLM_CHUNK_CHARS", 80)
    fake_openai_module.responses = [
        structured_response(discussion=["Teil 1 wurde beraten."]),
        structured_response(discussion=["Teil 2 wurde beraten."]),
        structured_response(
            discussion=["Die Beratung wurde zusammengefuehrt."],
            open_points=["Die Verwaltung liefert Zahlen nach."],
        ),
    ]

    transcript = "\n".join(
        [
            "SPEAKER_00: " + ("Haushaltsansatz und Begruendung. " * 2),
            "SPEAKER_01: " + ("Nachfrage zu Kosten und Fristen. " * 2),
        ]
    )

    result = summarize.summarize_segment("Haushalt", transcript, model="test-model")

    assert result.chunks_processed == 2
    assert "Die Beratung wurde zusammengefuehrt." in result.summary
    assert "Die Verwaltung liefert Zahlen nach." in result.summary

    calls = fake_openai_module.instances[0].calls
    assert len(calls) == 3
    assert "Teil 1 von 2" in calls[0]["messages"][1]["content"]
    assert "Teil 2 von 2" in calls[1]["messages"][1]["content"]
    assert "Teilnotizen" in calls[2]["messages"][1]["content"]


def test_summarize_segment_falls_back_to_freetext_on_malformed_structured_response(
    fake_openai_module,
):
    fake_openai_module.responses = [
        "Das ist kein JSON.",
        "Die Vorlage wurde beraten. Ein Beschluss wurde nicht gefasst.",
    ]

    result = summarize.summarize_segment(
        "Baugebiet",
        "SPEAKER_00: Die Vorlage wird beraten.",
        model="test-model",
    )

    assert result.summary == "Die Vorlage wurde beraten. Ein Beschluss wurde nicht gefasst."
    assert result.structured is None
    assert result.fallback_used is True
    assert result.chunks_processed == 1

    calls = fake_openai_module.instances[0].calls
    assert len(calls) == 2
    assert calls[1]["temperature"] == 0.3
    assert "Zusammenfassung:" in calls[1]["messages"][1]["content"]


def test_summarize_segment_retries_transient_llm_errors(
    fake_openai_module,
    monkeypatch,
):
    class ServerError(Exception):
        status_code = 503

    monkeypatch.setattr(summarize, "LLM_RETRY_BACKOFF_SECONDS", 0)
    fake_openai_module.responses = [
        ServerError("Service unavailable"),
        structured_response(discussion=["Die Beratung wurde fortgesetzt."]),
    ]

    result = summarize.summarize_segment(
        "Gebuehren",
        "SPEAKER_00: Wir beraten die Gebuehren.",
        model="test-model",
    )

    assert result.fallback_used is False
    assert "Die Beratung wurde fortgesetzt." in result.summary
    assert len(fake_openai_module.instances[0].calls) == 2

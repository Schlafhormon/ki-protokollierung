import summarize


def test_summarize_segment_uses_openai_client_and_returns_duration(
    fake_openai_module,
    monkeypatch,
):
    fake_openai_module.content = "Die Vorsitzende erlaeuterte den Sachverhalt."
    times = iter([100.0, 102.5])
    monkeypatch.setattr(summarize.time, "time", lambda: next(times))

    result = summarize.summarize_segment(
        "Haushalt",
        "SPEAKER_00: Wir beraten den Haushalt.",
        model="test-model",
        system_prompt="Formal zusammenfassen",
    )

    assert result.summary == "Die Vorsitzende erlaeuterte den Sachverhalt."
    assert result.duration_seconds == 2.5

    client = fake_openai_module.instances[0]
    assert client.kwargs["base_url"]
    request = client.calls[0]
    assert request["model"] == "test-model"
    assert request["temperature"] == 0.3
    assert request["max_tokens"] == 1024
    assert request["messages"][0] == {
        "role": "system",
        "content": "Formal zusammenfassen",
    }
    assert "TOP: Haushalt" in request["messages"][1]["content"]
    assert "SPEAKER_00: Wir beraten den Haushalt." in request["messages"][1]["content"]

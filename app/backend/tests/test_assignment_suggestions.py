from assignment_suggestions import TranscriptUtterance, suggest_assignments


def test_suggest_assignments_detects_explicit_moderator_transitions():
    transcript = [
        TranscriptUtterance("SPEAKER_00", "Ich eröffne die Sitzung und begrüße alle."),
        TranscriptUtterance("SPEAKER_01", "Vielen Dank."),
        TranscriptUtterance("SPEAKER_00", "Kommen wir zu TOP 2 Haushalt 2026."),
        TranscriptUtterance("SPEAKER_02", "Der Haushalt enthält Investitionen."),
        TranscriptUtterance("SPEAKER_00", "Als nächstes rufe ich TOP 3 Schulbau auf."),
        TranscriptUtterance("SPEAKER_03", "Beim Schulbau geht es um die Grundschule."),
    ]
    tops = ["1. Begrüßung", "2. Haushalt 2026", "3. Schulbau"]

    result = suggest_assignments(transcript, tops)

    assert result.suggested_assignments == [0, 0, 1, 1, 2, 2]
    assert [(segment.top_index, segment.start_index, segment.end_index) for segment in result.segments] == [
        (0, 0, 1),
        (1, 2, 3),
        (2, 4, 5),
    ]
    assert result.segments[1].confidence >= 0.7
    assert not result.segments[1].uncertain
    assert "TOP 2" in result.segments[1].reason


def test_suggest_assignments_marks_missing_boundaries_as_uncertain():
    transcript = [
        TranscriptUtterance("SPEAKER_00", "Ich eröffne die Sitzung."),
        TranscriptUtterance("SPEAKER_01", "Allgemeine Diskussion ohne klare Stichworte."),
        TranscriptUtterance("SPEAKER_02", "Weitere Wortmeldung."),
        TranscriptUtterance("SPEAKER_03", "Noch eine Wortmeldung."),
    ]
    tops = ["Begrüßung", "Haushalt", "Schulbau"]

    result = suggest_assignments(transcript, tops)

    assert len(result.segments) == 3
    assert result.uncertain_count == 2
    assert all(segment.uncertain for segment in result.segments[1:])
    assert result.suggested_assignments.count(None) == 0


def test_suggest_assignments_uses_topic_keywords_without_explicit_top_number():
    transcript = [
        TranscriptUtterance("MOD", "Begrüßung und Formalien."),
        TranscriptUtterance("MOD", "Dann kommen wir zum Haushalt und zur Finanzplanung."),
        TranscriptUtterance("A", "Die Finanzplanung ist nachvollziehbar."),
        TranscriptUtterance("MOD", "Weiter geht es mit dem Neubau der Grundschule."),
        TranscriptUtterance("B", "Der Neubau ist dringend."),
    ]
    tops = ["Begrüßung", "Haushalt und Finanzplanung", "Neubau Grundschule"]

    result = suggest_assignments(transcript, tops)

    assert result.suggested_assignments == [0, 1, 1, 2, 2]
    assert result.segments[1].transition_type in {"explicit", "keyword"}
    assert result.segments[2].confidence >= 0.55

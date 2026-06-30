from agenda_detection import detect_agenda_from_transcript, segment_known_agenda
from assignment_suggestions import TranscriptUtterance


def test_segment_known_agenda_detects_clear_top_announcements():
    transcript = [
        TranscriptUtterance("MOD", "Ich eröffne die Sitzung und begrüße alle."),
        TranscriptUtterance("A", "Vielen Dank."),
        TranscriptUtterance("MOD", "Kommen wir zu TOP 2 Haushalt 2026."),
        TranscriptUtterance("B", "Der Haushalt enthält Investitionen."),
        TranscriptUtterance("MOD", "Als nächstes rufe ich TOP 3 Schulbau auf."),
        TranscriptUtterance("C", "Beim Schulbau geht es um die Grundschule."),
    ]
    tops = ["Begrüßung", "Haushalt 2026", "Schulbau"]

    result = segment_known_agenda(transcript, tops)

    assert result.tops == tops
    assert result.assignments == [0, 0, 1, 1, 2, 2]
    assert [(segment.start_index, segment.end_index) for segment in result.segments] == [
        (0, 1),
        (2, 3),
        (4, 5),
    ]
    assert result.segments[1].confidence >= 0.7


def test_detect_agenda_from_transcript_without_known_tops():
    transcript = [
        TranscriptUtterance("MOD", "Kommen wir zu TOP 1 Haushalt 2026."),
        TranscriptUtterance("A", "Die Investitionen sind eingeplant."),
        TranscriptUtterance("MOD", "Als nächstes rufe ich TOP 2 Schulbau auf."),
        TranscriptUtterance("B", "Die Grundschule braucht mehr Räume."),
    ]

    result = detect_agenda_from_transcript(transcript)

    assert result.tops == ["Haushalt 2026", "Schulbau"]
    assert result.assignments == [0, 0, 1, 1]
    assert [(segment.top_title, segment.start_index, segment.end_index) for segment in result.segments] == [
        ("Haushalt 2026", 0, 1),
        ("Schulbau", 2, 3),
    ]
    assert result.strategy == "heuristic_transcript_fallback"


def test_segment_known_agenda_marks_uncertain_boundaries():
    transcript = [
        TranscriptUtterance("MOD", "Ich eröffne die Sitzung."),
        TranscriptUtterance("A", "Allgemeine Wortmeldung ohne Stichworte."),
        TranscriptUtterance("B", "Weitere Wortmeldung."),
        TranscriptUtterance("C", "Noch eine Wortmeldung."),
    ]

    result = segment_known_agenda(transcript, ["Begrüßung", "Haushalt", "Schulbau"])

    assert len(result.segments) == 3
    assert result.uncertain_count == 2
    assert all(segment.uncertain for segment in result.segments[1:])
    assert result.assignments.count(None) == 0


def test_llm_invalid_boundaries_are_repaired(fake_openai_module):
    fake_openai_module.content = """
    {
      "tops": [
        {
          "top_title": "Haushalt",
          "start_index": -5,
          "end_index": 99,
          "confidence": 0.91,
          "evidence_text": "Kommen wir zu TOP 1 Haushalt.",
          "uncertain": false
        },
        {
          "top_title": "Schulbau",
          "start_index": 0,
          "end_index": 1,
          "confidence": 0.88,
          "evidence_text": "TOP 2 Schulbau.",
          "uncertain": false
        }
      ]
    }
    """
    transcript = [
        TranscriptUtterance("MOD", "Kommen wir zu TOP 1 Haushalt."),
        TranscriptUtterance("A", "Der Haushalt wird beraten."),
        TranscriptUtterance("MOD", "TOP 2 Schulbau."),
    ]

    result = detect_agenda_from_transcript(transcript, model="test-model")

    assert result.strategy == "heuristic_transcript_llm_repaired"
    assert [(segment.start_index, segment.end_index) for segment in result.segments] == [
        (0, 1),
        (2, 2),
    ]
    assert result.assignments == [0, 0, 1]
    assert result.segments[0].uncertain
    assert result.segments[1].uncertain


def test_fallback_without_llm_returns_reviewable_assignments():
    transcript = [
        TranscriptUtterance("MOD", "TOP 1 Genehmigung der Niederschrift."),
        TranscriptUtterance("A", "Keine Einwände."),
        TranscriptUtterance("MOD", "TOP 2 Verschiedenes."),
    ]

    result = detect_agenda_from_transcript(transcript)

    assert result.tops == ["Genehmigung der Niederschrift", "Verschiedenes"]
    assert result.assignments == [0, 0, 1]
    assert result.strategy == "heuristic_transcript_fallback"

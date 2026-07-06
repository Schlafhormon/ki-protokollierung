from transcript_splitter import split_transcript_for_agenda_detection


def test_split_transcript_for_agenda_detection_splits_long_sentence_chunks():
    transcript = [
        {
            "speaker": "SPEAKER_04",
            "text": (
                "Das ist einstimmig. Kommen wir zum Tagesordnungspunkt 3. "
                "Wir haben heute einen neuen sachkundigen Einwohner."
            ),
            "start": 10.0,
            "end": 20.0,
        }
    ]

    result = split_transcript_for_agenda_detection(transcript)

    assert [line["text"] for line in result] == [
        "Das ist einstimmig.",
        "Kommen wir zum Tagesordnungspunkt 3.",
        "Wir haben heute einen neuen sachkundigen Einwohner.",
    ]
    assert all(line["speaker"] == "SPEAKER_04" for line in result)
    assert result[0]["start"] == 10.0
    assert result[0]["end"] == result[1]["start"]
    assert result[1]["end"] == result[2]["start"]
    assert result[2]["end"] == 20.0


def test_split_transcript_for_agenda_detection_does_not_use_top_phrases():
    transcript = [
        {
            "speaker": "SPEAKER_04",
            "text": (
                "Das ist einstimmig. Danach beraten wir den nächsten Abschnitt. "
                "Die Verwaltung erläutert den Sachstand."
            ),
            "start": 10.0,
            "end": 20.0,
        }
    ]

    result = split_transcript_for_agenda_detection(transcript)

    assert [line["text"] for line in result] == [
        "Das ist einstimmig.",
        "Danach beraten wir den nächsten Abschnitt.",
        "Die Verwaltung erläutert den Sachstand.",
    ]


def test_split_transcript_for_agenda_detection_keeps_short_line():
    transcript = [
        {
            "speaker": "SPEAKER_04",
            "text": "Kommen wir zum Tagesordnungspunkt 3.",
            "start": 10.0,
            "end": 20.0,
        }
    ]

    assert split_transcript_for_agenda_detection(transcript) == transcript

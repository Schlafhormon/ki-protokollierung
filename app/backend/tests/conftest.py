import sys
import types
from dataclasses import dataclass

import pytest


@dataclass
class FakeTranscriptionModels:
    device: str = "cpu"


@dataclass
class FakeTranscriptionResult:
    transcript: list[dict]
    audio_duration_seconds: float


fake_transcribe = types.ModuleType("transcribe")
fake_transcribe.TranscriptionModels = FakeTranscriptionModels
fake_transcribe.TranscriptionResult = FakeTranscriptionResult
fake_transcribe.WHISPER_MODEL = "test-whisper"
fake_transcribe.WHISPER_BATCH_SIZE = 1
fake_transcribe.load_models = lambda: FakeTranscriptionModels()
fake_transcribe._cleanup_memory = lambda device: None
fake_transcribe.transcribe_audio = lambda file_path, models, progress_callback=None: FakeTranscriptionResult(
    transcript=[],
    audio_duration_seconds=0,
)

sys.modules.setdefault("transcribe", fake_transcribe)


@pytest.fixture
def fake_openai_module(monkeypatch):
    class FakeCompletions:
        def __init__(self, owner):
            self.owner = owner

        def create(self, **kwargs):
            self.owner.calls.append(kwargs)
            return types.SimpleNamespace(
                choices=[
                    types.SimpleNamespace(
                        message=types.SimpleNamespace(content=self.owner.content)
                    )
                ]
            )

    class FakeOpenAI:
        instances = []
        content = ""

        def __init__(self, **kwargs):
            self.kwargs = kwargs
            self.calls = []
            self.content = type(self).content
            self.chat = types.SimpleNamespace(
                completions=FakeCompletions(self),
            )
            type(self).instances.append(self)

    module = types.ModuleType("openai")
    module.OpenAI = FakeOpenAI
    monkeypatch.setitem(sys.modules, "openai", module)
    return FakeOpenAI

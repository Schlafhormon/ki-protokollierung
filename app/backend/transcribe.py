"""
Transcription module using WhisperX with speaker diarization.

Setup:
1. Install WhisperX: uv sync --extra whisperx
2. Set HuggingFace token: export HF_TOKEN=your_token

Configuration via environment variables:
- HF_TOKEN: HuggingFace token for pyannote diarization (required)
- WHISPER_MODEL: Whisper model size (default: large-v2)
- WHISPER_DEVICE: Device to use - cuda/cpu (default: auto-detect)
- WHISPER_BATCH_SIZE: Batch size for transcription (default: 16)
- WHISPER_LANGUAGE: Language code (default: de)

NOTE: For GPU support, you must set LD_LIBRARY_PATH before starting the server:
  export LD_LIBRARY_PATH=$(python -c "import nvidia.cudnn; print(nvidia.cudnn.__path__[0])")/lib:$LD_LIBRARY_PATH
See: https://github.com/m-bain/whisperX/issues/902
"""

import os
import gc

# PyTorch 2.6+ changed torch.load() to use weights_only=True by default for security.
# WhisperX uses pyannote-audio 3.x which stores OmegaConf configs in checkpoints.
# pyannote-audio 4.x has the fix, but WhisperX pins to <4.0.0.
# This env var is the official workaround until WhisperX updates its dependency.
# See: https://github.com/m-bain/whisperX/issues/1304
os.environ.setdefault("TORCH_FORCE_NO_WEIGHTS_ONLY_LOAD", "1")
import re
import logging
from dataclasses import dataclass
from typing import List, Dict, Callable, Optional, Any

import torch
from whisperx.diarize import DiarizationPipeline
from speaker_recognition import (
    LocalSpeakerEmbedding,
    extract_local_speaker_embeddings,
    load_pyannote_embedding_inference,
    speaker_embedding_config_from_env,
)

# Configure logging with timestamps
logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s [%(levelname)s] %(message)s",
    datefmt="%Y-%m-%d %H:%M:%S",
)
logger = logging.getLogger(__name__)

# WhisperX configuration
WHISPER_MODEL = os.environ.get("WHISPER_MODEL", "large-v2")
WHISPER_DEVICE = os.environ.get("WHISPER_DEVICE", "auto")
WHISPER_BATCH_SIZE = int(os.environ.get("WHISPER_BATCH_SIZE", "16"))
WHISPER_LANGUAGE = os.environ.get("WHISPER_LANGUAGE", "de")


@dataclass
class TranscriptionModels:
    """Container for pre-loaded transcription models."""
    whisper_model: Any
    align_model: Any
    align_metadata: Any
    diarize_pipeline: Any
    device: str
    speaker_embedding_inference: Any | None = None


def load_models() -> TranscriptionModels:
    """
    Load all models required for transcription.
    Call this once at server startup to cache models in memory.

    Returns:
        TranscriptionModels containing all loaded models
    """
    logger.info("=" * 60)
    logger.info("LOADING TRANSCRIPTION MODELS (this may take several minutes)")
    logger.info("=" * 60)

    try:
        import whisperx
        import torch
        logger.info("Successfully imported whisperx and torch")
    except ImportError:
        logger.error("WhisperX not installed")
        raise RuntimeError(
            "WhisperX nicht installiert. Installieren Sie mit: uv sync --extra whisperx"
        )

    # Determine device
    if WHISPER_DEVICE == "auto":
        device = "cuda" if torch.cuda.is_available() else "cpu"
    else:
        device = WHISPER_DEVICE

    compute_type = "float16" if device == "cuda" else "int8"
    logger.info(f"Using device: {device}, compute_type: {compute_type}")

    # Load WhisperX model
    logger.info(f"[1/3] Loading WhisperX model: {WHISPER_MODEL}...")
    whisper_model = whisperx.load_model(
        WHISPER_MODEL,
        device,
        compute_type=compute_type,
        language=WHISPER_LANGUAGE,
    )
    logger.info(f"[1/3] WhisperX model loaded successfully")

    # Load alignment model
    logger.info(f"[2/3] Loading alignment model for language: {WHISPER_LANGUAGE}...")
    align_model, align_metadata = whisperx.load_align_model(
        language_code=WHISPER_LANGUAGE,
        device=device,
    )
    logger.info("[2/3] Alignment model loaded successfully")

    # Load diarization pipeline
    hf_token = os.environ.get("HF_TOKEN")
    models_precached = os.environ.get("MODELS_PRECACHED") == "1"

    # When models are pre-cached (Docker image with bundled models), HF_TOKEN is not required
    # PyAnnote will load from the local cache without authentication
    if not hf_token and not models_precached:
        logger.error("HF_TOKEN not set and models not pre-cached")
        raise RuntimeError(
            "HuggingFace Token nicht gesetzt und Modelle nicht vorinstalliert. "
            "Setzen Sie die HF_TOKEN Umgebungsvariable oder verwenden Sie das "
            "vorgefertigte Docker-Image mit vorinstallierten Modellen."
        )

    logger.info("[3/3] Loading diarization pipeline...")
    if models_precached:
        logger.info("Using pre-cached models (no HF_TOKEN required)")
    diarize_pipeline = DiarizationPipeline(
        use_auth_token=hf_token if hf_token else None,
        device=device,
    )
    logger.info("[3/3] Diarization pipeline loaded successfully")

    speaker_embedding_config = speaker_embedding_config_from_env()
    speaker_embedding_inference = load_pyannote_embedding_inference(
        device=device,
        config=speaker_embedding_config,
    )

    logger.info("=" * 60)
    logger.info("ALL MODELS LOADED SUCCESSFULLY - Server ready for requests")
    logger.info("=" * 60)

    return TranscriptionModels(
        whisper_model=whisper_model,
        align_model=align_model,
        align_metadata=align_metadata,
        diarize_pipeline=diarize_pipeline,
        device=device,
        speaker_embedding_inference=speaker_embedding_inference,
    )


def _cleanup_memory(device: str) -> None:
    """Force memory cleanup after transcription to prevent OOM on subsequent runs."""
    gc.collect()
    try:
        if device == "cuda" and torch.cuda.is_available():
            torch.cuda.synchronize()  # Wait for all CUDA ops to complete
            torch.cuda.empty_cache()
            logger.info("GPU memory cache cleared")
    except Exception as e:
        logger.warning(f"Failed to clear GPU cache: {e}")


@dataclass
class TranscriptionResult:
    """Result from transcription including metrics."""
    transcript: List[Dict[str, Any]]
    audio_duration_seconds: float
    speaker_embeddings: List[LocalSpeakerEmbedding] | None = None


def transcribe_audio(
    file_path: str,
    models: TranscriptionModels,
    progress_callback: Optional[Callable[[int, str], None]] = None,
) -> TranscriptionResult:
    """
    Transcribe audio file with speaker diarization using WhisperX.

    Args:
        file_path: Path to audio file (MP3, WAV, M4A)
        models: Pre-loaded TranscriptionModels from load_models()
        progress_callback: Optional callback for progress updates (progress%, message)

    Returns:
        TranscriptionResult with transcript and audio duration
    """
    import whisperx

    logger.info(f"Starting transcription for: {file_path}")

    # Initialize variables for cleanup tracking
    audio = None
    result = None
    diarize_segments = None

    try:
        if progress_callback:
            progress_callback(5, "Lade Audio...")

        # Load audio
        logger.info(f"Loading audio file: {file_path}")
        audio = whisperx.load_audio(file_path)
        audio_duration_seconds = len(audio) / 16000
        logger.info(f"Audio loaded, duration: {audio_duration_seconds:.1f} seconds")

        if progress_callback:
            progress_callback(15, "Transkription läuft...")

        # Transcribe using pre-loaded model
        logger.info(f"Starting transcription with batch_size={WHISPER_BATCH_SIZE}...")
        result = models.whisper_model.transcribe(
            audio,
            batch_size=WHISPER_BATCH_SIZE,
            language=WHISPER_LANGUAGE,
        )
        logger.info(f"Transcription complete, found {len(result.get('segments', []))} segments")

        if progress_callback:
            progress_callback(45, "Alignment läuft...")

        # Align using pre-loaded model
        logger.info("Running alignment...")
        result = whisperx.align(
            result["segments"],
            models.align_model,
            models.align_metadata,
            audio,
            models.device,
            return_char_alignments=False,
        )
        logger.info("Alignment complete")

        if progress_callback:
            progress_callback(65, "Sprechererkennung läuft...")

        # Speaker diarization using pre-loaded pipeline
        logger.info("Running speaker diarization...")
        diarize_segments = models.diarize_pipeline(audio)
        logger.info("Diarization complete")

        logger.info("Extracting local speaker embeddings...")
        speaker_embeddings = extract_local_speaker_embeddings(
            audio=audio,
            diarize_segments=diarize_segments,
            embedding_inference=getattr(models, "speaker_embedding_inference", None),
        )
        logger.info(
            "Extracted speaker embeddings for %s local speakers",
            len(speaker_embeddings),
        )

        if progress_callback:
            progress_callback(85, "Segmente werden zusammengeführt...")

        # Assign speakers to segments
        logger.info("Assigning speakers to segments...")
        result = whisperx.assign_word_speakers(diarize_segments, result)
        logger.info("Speaker assignment complete")

        if progress_callback:
            progress_callback(95, "Transkript wird erstellt...")

        # Convert to our format and merge consecutive segments from same speaker
        # Keep timestamps for audio sync - extend end time when merging
        logger.info("Creating transcript output...")
        transcript = []
        raw_segment_count = len(result["segments"])
        for segment in result["segments"]:
            speaker = segment.get("speaker", "UNKNOWN")
            text = segment.get("text", "").strip()
            if text:
                # Merge with previous segment if same speaker
                if transcript and transcript[-1]["speaker"] == speaker:
                    transcript[-1]["text"] += " " + text
                    transcript[-1]["end"] = segment.get("end", transcript[-1]["end"])
                else:
                    transcript.append({
                        "speaker": speaker,
                        "text": text,
                        "start": segment.get("start", 0.0),
                        "end": segment.get("end", 0.0),
                    })

        logger.info(f"Transcription finished: {len(transcript)} lines (merged from {raw_segment_count} segments)")
        return TranscriptionResult(
            transcript=transcript,
            audio_duration_seconds=audio_duration_seconds,
            speaker_embeddings=speaker_embeddings,
        )

    finally:
        # Explicit memory cleanup
        logger.info("Cleaning up transcription memory...")
        del audio
        del result
        del diarize_segments
        _cleanup_memory(models.device)
        logger.info("Memory cleanup complete")


def parse_transcript_file(file_path: str) -> List[Dict[str, str]]:
    """
    Parse existing transcript file in [SPEAKER_XX]: text format.
    Useful for loading pre-generated transcripts.
    """
    transcript = []
    pattern = r"\[SPEAKER_(\d+)\]:\s*(.+)"

    with open(file_path, "r", encoding="utf-8") as f:
        for line in f:
            line = line.strip()
            if not line:
                continue

            match = re.match(pattern, line)
            if match:
                speaker_id = match.group(1)
                text = match.group(2).strip()
                if text:
                    transcript.append({
                        "speaker": f"SPEAKER_{speaker_id}",
                        "text": text,
                    })

    return transcript

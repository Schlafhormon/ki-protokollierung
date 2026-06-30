/**
 * API client for the Protokollierungsassistenz backend
 */

import type {
  SessionResponse,
  SessionSavePayload,
  TranscriptLine,
  TranscriptionJob,
} from "./types";

const API_BASE = import.meta.env.VITE_API_URL || "";

/**
 * Start a transcription job by uploading an audio file.
 */
export async function startTranscription(
  audioFile: File,
  sessionId?: string | null
): Promise<TranscriptionJob> {
  const formData = new FormData();
  formData.append("audio", audioFile);
  if (sessionId) {
    formData.append("session_id", sessionId);
  }

  const response = await fetch(`${API_BASE}/api/transcribe`, {
    method: "POST",
    body: formData,
  });

  if (!response.ok) {
    const error = await response.json();
    throw new Error(error.detail || "Fehler beim Starten der Transkription");
  }

  return response.json();
}

/**
 * Get the status of a transcription job.
 */
export async function getTranscriptionStatus(
  jobId: string
): Promise<TranscriptionJob> {
  const response = await fetch(`${API_BASE}/api/transcribe/${jobId}`);

  if (!response.ok) {
    const error = await response.json();
    throw new Error(error.detail || "Fehler beim Abrufen des Status");
  }

  return response.json();
}

/**
 * Create or update an editing session.
 */
export async function saveSession(
  payload: SessionSavePayload
): Promise<SessionResponse> {
  const sessionId = payload.session_id?.trim();
  const response = await fetch(
    sessionId ? `${API_BASE}/api/sessions/${sessionId}` : `${API_BASE}/api/sessions`,
    {
      method: sessionId ? "PUT" : "POST",
      headers: {
        "Content-Type": "application/json",
      },
      body: JSON.stringify(payload),
    }
  );

  if (!response.ok) {
    const error = await response.json();
    throw new Error(error.detail || "Fehler beim Speichern der Sitzung");
  }

  return response.json();
}

/**
 * Load a persisted editing session.
 */
export async function loadSession(sessionId: string): Promise<SessionResponse> {
  const response = await fetch(`${API_BASE}/api/sessions/${sessionId}`);

  if (!response.ok) {
    const error = await response.json();
    throw new Error(error.detail || "Fehler beim Laden der Sitzung");
  }

  return response.json();
}

/**
 * Cancel a pending or running transcription job.
 */
export async function cancelTranscription(
  jobId: string
): Promise<TranscriptionJob> {
  const response = await fetch(`${API_BASE}/api/transcribe/${jobId}/cancel`, {
    method: "POST",
  });

  if (!response.ok) {
    const error = await response.json();
    throw new Error(error.detail || "Fehler beim Abbrechen der Transkription");
  }

  return response.json();
}

/**
 * Poll for transcription completion.
 * Returns the full TranscriptionJob including audio_url for playback.
 */
export async function pollTranscription(
  jobId: string,
  onProgress?: (progress: number, message: string) => void
): Promise<TranscriptionJob> {
  while (true) {
    const status = await getTranscriptionStatus(jobId);

    if (onProgress) {
      onProgress(status.progress, status.message);
    }

    if (status.status === "completed") {
      return status;
    }

    if (status.status === "failed") {
      throw new Error(status.error || "Transkription fehlgeschlagen");
    }

    if (status.status === "cancelled") {
      throw new Error(status.message || "Transkription abgebrochen");
    }

    // Wait before polling again
    await new Promise((resolve) => setTimeout(resolve, 1000));
  }
}

/**
 * Options for summary generation.
 */
export interface SummarizeOptions {
  model?: string;
  systemPrompt?: string;
}

/**
 * Result from summary generation including timing.
 */
export interface SummarizeResult {
  summary: string;
  durationSeconds: number;
}

/**
 * Generate a summary for a TOP segment.
 */
export async function generateSummary(
  topTitle: string,
  lines: TranscriptLine[],
  options?: SummarizeOptions
): Promise<SummarizeResult> {
  const response = await fetch(`${API_BASE}/api/summarize`, {
    method: "POST",
    headers: {
      "Content-Type": "application/json",
    },
    body: JSON.stringify({
      top_title: topTitle,
      lines: lines,
      model: options?.model,
      system_prompt: options?.systemPrompt,
    }),
  });

  if (!response.ok) {
    const error = await response.json();
    throw new Error(error.detail || "Fehler bei der Zusammenfassung");
  }

  const data = await response.json();
  return {
    summary: data.summary,
    durationSeconds: data.duration_seconds,
  };
}

/**
 * Options for TOP extraction from PDF.
 */
export interface ExtractTOPsOptions {
  model?: string;
  systemPrompt?: string;
}

/**
 * Extract TOPs (agenda items) from a PDF meeting invitation.
 */
export async function extractTOPsFromPDF(
  pdfFile: File,
  options?: ExtractTOPsOptions
): Promise<string[]> {
  const formData = new FormData();
  formData.append("pdf", pdfFile);

  // Add optional parameters as form fields
  if (options?.model) {
    formData.append("model", options.model);
  }
  if (options?.systemPrompt) {
    formData.append("system_prompt", options.systemPrompt);
  }

  const response = await fetch(`${API_BASE}/api/extract-tops`, {
    method: "POST",
    body: formData,
  });

  if (!response.ok) {
    const error = await response.json();
    throw new Error(error.detail || "Fehler beim Extrahieren der TOPs");
  }

  const data = await response.json();
  return data.tops;
}

/**
 * Check if the backend is available.
 */
export async function checkBackendHealth(): Promise<boolean> {
  try {
    console.log("Checking backend health at", API_BASE || "(relative)");
    const response = await fetch(`${API_BASE}/health`);
    return response.ok;
  } catch {
    return false;
  }
}

/**
 * Session complete telemetry data.
 */
export interface SessionCompleteData {
  jobId: string;
  topCount: number;
  protocolCharCount: number;
  summarizationDurationSeconds: number;
  llmModel: string;
  systemPrompt: string;
}

/**
 * Report session completion for telemetry.
 * Called when the user exports the protocol.
 */
export async function reportSessionComplete(
  data: SessionCompleteData
): Promise<void> {
  try {
    const response = await fetch(`${API_BASE}/api/telemetry/session-complete`, {
      method: "POST",
      headers: {
        "Content-Type": "application/json",
      },
      body: JSON.stringify({
        job_id: data.jobId,
        top_count: data.topCount,
        protocol_char_count: data.protocolCharCount,
        summarization_duration_seconds: data.summarizationDurationSeconds,
        llm_model: data.llmModel,
        system_prompt: data.systemPrompt,
      }),
    });

    if (!response.ok) {
      console.warn("Failed to send telemetry:", await response.text());
    }
  } catch (error) {
    // Silently fail - telemetry should not block user workflow
    console.warn("Failed to send telemetry:", error);
  }
}

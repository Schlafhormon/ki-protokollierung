/**
 * API client for the Protokollierungsassistenz backend
 */

import type {
  AssignmentSuggestionsResponse,
  ExportFormat,
  ExportMetadata,
  SessionResponse,
  SessionSavePayload,
  SpeakerObservation,
  SpeakerProfile,
  SummaryReviewWarning,
  SummarySourceLink,
  StructuredSummary,
  TranscriptLine,
  TranscriptionJob,
} from "./types";

const API_BASE = import.meta.env.VITE_API_URL || "";

async function readApiError(response: Response, fallback: string): Promise<Error> {
  try {
    const error = await response.json();
    return new Error(error.detail || fallback);
  } catch {
    return new Error(fallback);
  }
}

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
  structured?: StructuredSummary | null;
  sourceLinks: SummarySourceLink[];
  reviewWarnings: SummaryReviewWarning[];
  fallbackUsed: boolean;
  chunksProcessed: number;
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
    structured: data.structured ?? null,
    sourceLinks: data.source_links ?? [],
    reviewWarnings: data.review_warnings ?? [],
    fallbackUsed: Boolean(data.fallback_used),
    chunksProcessed: data.chunks_processed ?? 1,
  };
}

/**
 * Suggest reviewable TOP assignments for a transcript.
 */
export async function generateAssignmentSuggestions(
  tops: string[],
  transcript: TranscriptLine[]
): Promise<AssignmentSuggestionsResponse> {
  const response = await fetch(`${API_BASE}/api/assignment-suggestions`, {
    method: "POST",
    headers: {
      "Content-Type": "application/json",
    },
    body: JSON.stringify({
      tops,
      transcript,
    }),
  });

  if (!response.ok) {
    const error = await response.json();
    throw new Error(error.detail || "Fehler beim Erzeugen der Zuordnungsvorschläge");
  }

  return response.json();
}

export interface ListSpeakerProfilesOptions {
  scope?: string | null;
  includeArchived?: boolean;
}

export interface SpeakerProfilePayload {
  displayName: string;
  scope?: string | null;
}

export async function listSpeakerProfiles(
  options: ListSpeakerProfilesOptions = {}
): Promise<SpeakerProfile[]> {
  const params = new URLSearchParams();
  if (options.scope) {
    params.set("scope", options.scope);
  }
  if (options.includeArchived) {
    params.set("include_archived", "true");
  }

  const query = params.toString();
  const response = await fetch(
    `${API_BASE}/api/speaker-profiles${query ? `?${query}` : ""}`
  );

  if (!response.ok) {
    throw await readApiError(response, "Fehler beim Laden der Sprecherprofile");
  }

  return response.json();
}

export async function createSpeakerProfile(
  payload: SpeakerProfilePayload
): Promise<SpeakerProfile> {
  const response = await fetch(`${API_BASE}/api/speaker-profiles`, {
    method: "POST",
    headers: {
      "Content-Type": "application/json",
    },
    body: JSON.stringify({
      display_name: payload.displayName,
      scope: payload.scope,
    }),
  });

  if (!response.ok) {
    throw await readApiError(response, "Fehler beim Anlegen des Sprecherprofils");
  }

  return response.json();
}

export async function updateSpeakerProfile(
  profileId: string,
  payload: Partial<SpeakerProfilePayload>
): Promise<SpeakerProfile> {
  const body: Record<string, string | null> = {};
  if (payload.displayName !== undefined) {
    body.display_name = payload.displayName;
  }
  if (payload.scope !== undefined) {
    body.scope = payload.scope;
  }

  const response = await fetch(`${API_BASE}/api/speaker-profiles/${profileId}`, {
    method: "PUT",
    headers: {
      "Content-Type": "application/json",
    },
    body: JSON.stringify(body),
  });

  if (!response.ok) {
    throw await readApiError(response, "Fehler beim Aktualisieren des Sprecherprofils");
  }

  return response.json();
}

export async function archiveSpeakerProfile(profileId: string): Promise<SpeakerProfile> {
  const response = await fetch(`${API_BASE}/api/speaker-profiles/${profileId}`, {
    method: "DELETE",
  });

  if (!response.ok) {
    throw await readApiError(response, "Fehler beim Archivieren des Sprecherprofils");
  }

  return response.json();
}

export async function listSpeakerObservations(
  sessionId: string
): Promise<SpeakerObservation[]> {
  const response = await fetch(
    `${API_BASE}/api/sessions/${sessionId}/speaker-observations`
  );

  if (!response.ok) {
    throw await readApiError(response, "Fehler beim Laden der Sprecher-Erkennungen");
  }

  return response.json();
}

export interface ConfirmSpeakerObservationOptions {
  profileId?: string | null;
  confidence?: number | null;
}

export async function confirmSpeakerObservation(
  sessionId: string,
  observationId: number,
  options: ConfirmSpeakerObservationOptions = {}
): Promise<SpeakerObservation> {
  const response = await fetch(
    `${API_BASE}/api/sessions/${sessionId}/speaker-observations/${observationId}/confirm`,
    {
      method: "POST",
      headers: {
        "Content-Type": "application/json",
      },
      body: JSON.stringify({
        profile_id: options.profileId,
        confidence: options.confidence,
      }),
    }
  );

  if (!response.ok) {
    throw await readApiError(response, "Fehler beim Bestätigen der Sprecher-Erkennung");
  }

  return response.json();
}

export async function rejectSpeakerObservation(
  sessionId: string,
  observationId: number
): Promise<SpeakerObservation> {
  const response = await fetch(
    `${API_BASE}/api/sessions/${sessionId}/speaker-observations/${observationId}/reject`,
    {
      method: "POST",
    }
  );

  if (!response.ok) {
    throw await readApiError(response, "Fehler beim Ablehnen der Sprecher-Erkennung");
  }

  return response.json();
}

export interface ManualSpeakerObservationPayload {
  localSpeakerId: string;
  profileId?: string | null;
  displayName?: string | null;
  scope?: string | null;
  confidence?: number | null;
  observationId?: number | null;
}

export async function createManualSpeakerObservation(
  sessionId: string,
  payload: ManualSpeakerObservationPayload
): Promise<SpeakerObservation> {
  const response = await fetch(
    `${API_BASE}/api/sessions/${sessionId}/speaker-observations/manual`,
    {
      method: "POST",
      headers: {
        "Content-Type": "application/json",
      },
      body: JSON.stringify({
        local_speaker_id: payload.localSpeakerId,
        profile_id: payload.profileId,
        display_name: payload.displayName,
        scope: payload.scope,
        confidence: payload.confidence,
        observation_id: payload.observationId,
      }),
    }
  );

  if (!response.ok) {
    throw await readApiError(response, "Fehler beim Speichern der Sprecher-Zuordnung");
  }

  return response.json();
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

export interface ProtocolExportPayload {
  format: ExportFormat;
  metadata: ExportMetadata;
  tops: string[];
  transcript: TranscriptLine[];
  assignments: (number | null)[];
  speakerNames: Record<string, string>;
  summaries: Record<number, string>;
  summaryReviews?: Record<number, unknown>;
}

export async function exportProtocol(payload: ProtocolExportPayload): Promise<Blob> {
  const response = await fetch(`${API_BASE}/api/export`, {
    method: "POST",
    headers: {
      "Content-Type": "application/json",
    },
    body: JSON.stringify({
      format: payload.format,
      metadata: {
        committee: payload.metadata.committee,
        date: payload.metadata.date,
        location: payload.metadata.location,
        title: payload.metadata.title,
        participants: payload.metadata.participants,
      },
      appendix: {
        include_speaker_list: payload.metadata.includeSpeakerList,
        include_transcript_excerpt: payload.metadata.includeTranscriptExcerpt,
        include_generation_note: payload.metadata.includeGenerationNote,
      },
      tops: payload.tops,
      transcript: payload.transcript,
      assignments: payload.assignments,
      speaker_names: payload.speakerNames,
      summaries: payload.summaries,
      summary_reviews: payload.summaryReviews ?? {},
    }),
  });

  if (!response.ok) {
    const error = await response.json();
    throw new Error(error.detail || "Fehler beim Exportieren des Protokolls");
  }

  return response.blob();
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
  telemetryConsent: boolean;
  jobId: string;
  topCount: number;
  protocolCharCount: number;
  summarizationDurationSeconds: number;
  llmModel: string;
  systemPromptKind: "default" | "custom" | "generic";
}

/**
 * Report session completion for telemetry.
 * Called when the user exports the protocol.
 */
export async function reportSessionComplete(
  data: SessionCompleteData
): Promise<void> {
  if (!data.telemetryConsent) {
    return;
  }

  try {
    const response = await fetch(`${API_BASE}/api/telemetry/session-complete`, {
      method: "POST",
      headers: {
        "Content-Type": "application/json",
      },
      body: JSON.stringify({
        telemetry_consent: true,
        job_id: data.jobId,
        top_count: data.topCount,
        protocol_char_count: data.protocolCharCount,
        summarization_duration_seconds: data.summarizationDurationSeconds,
        llm_model: data.llmModel,
        system_prompt_kind: data.systemPromptKind,
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

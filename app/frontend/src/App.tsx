import { useCallback, useEffect, useMemo, useRef, useState } from "react";
import Layout from "./components/Layout";
import StepIndicator from "./components/StepIndicator";
import UploadStep from "./components/UploadStep";
import ProcessingStep from "./components/ProcessingStep";
import AssignmentStep from "./components/AssignmentStep";
import SummaryStep from "./components/SummaryStep";
import LLMSettingsPanel, {
  DEFAULT_LLM_SETTINGS,
  type LLMSettings,
} from "./components/LLMSettingsPanel";
import {
  startTranscription as apiStartTranscription,
  pollTranscription,
  startPipeline as apiStartPipeline,
  pollPipeline,
  getPipelineStatus,
  getPipelineResult,
  cancelPipeline,
  generateSummary,
  detectAgenda,
  checkBackendHealth,
  saveSession,
  loadSession,
} from "./api";
import type {
  AgendaDetectionResponse,
  ExportMetadata,
  PipelineJob,
  PipelineResultResponse,
  SessionResponse,
  SessionSavePayload,
  SummaryReview,
  TranscriptLine,
} from "./types";

// LocalStorage key for LLM settings
const LLM_SETTINGS_KEY = "llm-settings";
const ACTIVE_SESSION_KEY = "active-session-id";
const ACTIVE_PIPELINE_KEY = "active-pipeline-id";
const SESSION_DRAFT_KEY = "active-session-draft";
const SPEAKER_MEMORY_OPT_IN_KEY = "speaker-memory-opt-in";

// Implicit TOP title when no TOPs are defined
const DEFAULT_TOP_TITLE = "Gesamtes Gespräch";
const EMPTY_TOPS = ["", "", ""];
const DEFAULT_EXPORT_METADATA: ExportMetadata = {
  committee: "",
  date: "",
  location: "",
  title: "Sitzungsprotokoll",
  participants: [],
  includeSpeakerList: true,
  includeTranscriptExcerpt: false,
  includeGenerationNote: true,
};

// Generic system prompt for conversations without TOPs
const GENERIC_SUMMARY_PROMPT = `Du bist ein Experte für die Zusammenfassung von Gesprächen und Audioaufnahmen.

Deine Aufgabe ist es, aus einem Transkript eine klare und strukturierte Zusammenfassung zu erstellen.

STIL:
- Sachlich und gut lesbar
- Dritte Person
- Paraphrasieren statt wörtlich zitieren

INHALT:
- Wesentliche Themen und Diskussionspunkte
- Wichtige Aussagen und Positionen der Teilnehmer
- Getroffene Entscheidungen oder Vereinbarungen
- Offene Punkte oder nächste Schritte

FORMAT:
- Kurze Gespräche (< 10 Äußerungen): 1-2 Absätze
- Mittlere Gespräche (10-50 Äußerungen): 2-3 Absätze
- Lange Gespräche (> 50 Äußerungen): 3-5 Absätze
- Direkt mit Inhalt beginnen, keine Einleitung
- NUR Fließtext, KEINE Markdown-Formatierung (keine **, keine #)
`;

interface SessionDraft extends SessionSavePayload {
  audio_url?: string | null;
  pipeline_id?: string | null;
  summary_input_fingerprint?: string | null;
}

function withApiBase(url?: string | null): string | null {
  if (!url) return null;
  if (/^https?:\/\//i.test(url)) return url;
  const baseUrl = import.meta.env.VITE_API_URL || "";
  return `${baseUrl}${url}`;
}

function normalizeSummaries(
  summaries: Record<number, string> | Record<string, string> | undefined
): Record<number, string> {
  const normalized: Record<number, string> = {};
  Object.entries(summaries ?? {}).forEach(([key, value]) => {
    const index = Number(key);
    if (Number.isFinite(index)) {
      normalized[index] = value;
    }
  });
  return normalized;
}

function normalizeSummaryReviews(
  reviews:
    | Record<number, SummaryReview>
    | Record<string, SummaryReview>
    | undefined
): Record<number, SummaryReview> {
  const normalized: Record<number, SummaryReview> = {};
  Object.entries(reviews ?? {}).forEach(([key, value]) => {
    const index = Number(key);
    if (Number.isFinite(index) && value) {
      normalized[index] = {
        structured: value.structured ?? null,
        source_links: value.source_links ?? [],
        review_warnings: value.review_warnings ?? [],
        fallback_used: value.fallback_used,
        chunks_processed: value.chunks_processed,
      };
    }
  });
  return normalized;
}

function buildSummaryInputFingerprint(
  tops: string[],
  transcript: TranscriptLine[],
  assignments: (number | null)[],
  speakerNames: Record<string, string>
): string {
  const normalizedSpeakerNames: Record<string, string> = {};
  Object.keys(speakerNames)
    .sort()
    .forEach((speaker) => {
      normalizedSpeakerNames[speaker] = speakerNames[speaker]?.trim() ?? "";
    });
  return JSON.stringify({
    tops: tops.map((top) => top.trim()).filter(Boolean),
    transcript: transcript.map((line) => ({
      speaker: line.speaker,
      text: line.text,
      start: line.start,
      end: line.end,
    })),
    assignments,
    speakerNames: normalizedSpeakerNames,
  });
}

function getSessionSummaryInputFingerprint(
  session: SessionResponse | SessionDraft
): string | null {
  if (!hasAnySummary(session)) {
    return null;
  }
  return (
    (session as SessionDraft).summary_input_fingerprint ??
    buildSummaryInputFingerprint(
      session.tops ?? [],
      session.transcript ?? [],
      session.assignments ?? [],
      session.speaker_names ?? {}
    )
  );
}

function hasFreshSessionSummaries(session: SessionResponse | SessionDraft): boolean {
  const fingerprint = getSessionSummaryInputFingerprint(session);
  if (!fingerprint) {
    return false;
  }
  return (
    fingerprint ===
    buildSummaryInputFingerprint(
      session.tops ?? [],
      session.transcript ?? [],
      session.assignments ?? [],
      session.speaker_names ?? {}
    )
  );
}

function hasAgendaUncertainty(
  agendaDetection?: AgendaDetectionResponse | null
): boolean {
  if (!agendaDetection) {
    return false;
  }
  return (
    (agendaDetection.uncertain_count ?? 0) > 0 ||
    (agendaDetection.segments ?? []).some((segment) => segment.uncertain)
  );
}

function hasReviewUncertainty(
  session: SessionResponse,
  warnings: string[] = [],
  agendaDetection?: AgendaDetectionResponse | null
): boolean {
  const transcriptLength = session.transcript?.length ?? 0;
  const assignments = session.assignments ?? [];
  if (transcriptLength > 0 && session.skipped_assignment) {
    return true;
  }
  if (transcriptLength > 0 && !session.skipped_assignment) {
    if (assignments.length !== transcriptLength) {
      return true;
    }
    if (assignments.some((assignment) => assignment === null || assignment === undefined)) {
      return true;
    }
  }

  if (warnings.length > 0) {
    return true;
  }

  if (hasAgendaUncertainty(agendaDetection)) {
    return true;
  }

  const reviews = normalizeSummaryReviews(session.summary_reviews);
  return Object.values(reviews).some((review) => {
    const warningsForTop = review.review_warnings ?? [];
    const hasBlockingWarning = warningsForTop.some((warning) =>
      ["warning", "error"].includes(String(warning.severity ?? "").toLowerCase())
    );
    const hasStructuredUncertainty = Boolean(
      review.structured?.uncertainties?.some((item) => item.trim())
    );
    return hasBlockingWarning || hasStructuredUncertainty;
  });
}

function hasAnySummary(session: SessionResponse | SessionDraft): boolean {
  return Object.values(session.summaries ?? {}).some(
    (summary) => typeof summary === "string" && summary.trim() !== ""
  );
}

export default function App() {
  // Current step in wizard
  const [currentStep, setCurrentStep] = useState(1);
  const [isProcessing, setIsProcessing] = useState(false);
  const [processingProgress, setProcessingProgress] = useState(0);
  const [processingStatus, setProcessingStatus] = useState("");
  const [processingError, setProcessingError] = useState<string | null>(null);

  // Backend status
  const [backendAvailable, setBackendAvailable] = useState<boolean | null>(
    null
  );

  // Data state
  const [audioFile, setAudioFile] = useState<File | null>(null);
  const [pdfFile, setPdfFile] = useState<File | null>(null);
  const [tops, setTops] = useState<string[]>(EMPTY_TOPS);
  const [transcript, setTranscript] = useState<TranscriptLine[]>([]);
  const [assignments, setAssignments] = useState<(number | null)[]>([]);
  const [agendaDetection, setAgendaDetection] = useState<AgendaDetectionResponse | null>(null);
  const [agendaDetectionError, setAgendaDetectionError] = useState<string | null>(null);
  const [summaries, setSummaries] = useState<Record<number, string>>({});
  const [summaryReviews, setSummaryReviews] = useState<Record<number, SummaryReview>>({});
  const [summaryInputFingerprint, setSummaryInputFingerprint] = useState<string | null>(null);
  const [exportMetadata, setExportMetadata] = useState<ExportMetadata>(DEFAULT_EXPORT_METADATA);
  const [isGeneratingSummary, setIsGeneratingSummary] = useState(false);
  const [audioUrl, setAudioUrl] = useState<string | null>(null);
  const [speakerNames, setSpeakerNames] = useState<Record<string, string>>({});
  const [skippedAssignment, setSkippedAssignment] = useState(false);
  const [skipAgendaDetection, setSkipAgendaDetection] = useState(false);
  const [autoDetectTopsFromPdf, setAutoDetectTopsFromPdf] = useState(false);
  const [sessionId, setSessionId] = useState<string | null>(null);
  const [pipelineId, setPipelineId] = useState<string | null>(null);
  const [pipelineJob, setPipelineJob] = useState<PipelineJob | null>(null);
  const [pipelineNotice, setPipelineNotice] = useState<string | null>(null);
  const [directProtocolAvailable, setDirectProtocolAvailable] = useState(false);
  const [restoreCandidate, setRestoreCandidate] = useState<{
    sessionId: string | null;
    pipelineId: string | null;
    draft: SessionDraft | null;
  } | null>(null);
  const [isRestoringSession, setIsRestoringSession] = useState(false);
  const [sessionMessage, setSessionMessage] = useState<string | null>(null);
  const lastSavedPayloadRef = useRef<string | null>(null);
  const saveTimerRef = useRef<number | null>(null);

  // Helper to apply speaker name mappings to transcript lines for summarization
  const applySpeakerNames = (lines: TranscriptLine[]): TranscriptLine[] => {
    return lines.map((line) => ({
      ...line,
      speaker: speakerNames[line.speaker]?.trim() || line.speaker,
    }));
  };

  const currentSummaryInputFingerprint = useMemo(
    () => buildSummaryInputFingerprint(tops, transcript, assignments, speakerNames),
    [assignments, speakerNames, tops, transcript]
  );
  const summariesAreFresh =
    summaryInputFingerprint !== null &&
    summaryInputFingerprint === currentSummaryInputFingerprint;
  const hasFreshSummariesInState = hasAnySummary({
    tops,
    transcript,
    assignments,
    speaker_names: speakerNames,
    summaries,
    skipped_assignment: skippedAssignment,
  }) && summariesAreFresh;

  const [jobId, setJobId] = useState<string | null>(null);
  const [rememberSpeakers, setRememberSpeakers] = useState<boolean>(() => {
    try {
      return localStorage.getItem(SPEAKER_MEMORY_OPT_IN_KEY) === "true";
    } catch {
      return false;
    }
  });

  // LLM Settings state
  const [isSettingsOpen, setIsSettingsOpen] = useState(false);
  const [llmSettings, setLlmSettings] = useState<LLMSettings>(() => {
    // Load from localStorage on initial render
    try {
      const saved = localStorage.getItem(LLM_SETTINGS_KEY);
      if (saved) {
        return JSON.parse(saved);
      }
    } catch (e) {
      console.error("Failed to load LLM settings from localStorage:", e);
    }
    return DEFAULT_LLM_SETTINGS;
  });

  const buildSessionPayload = useCallback(
    (overrides: Partial<SessionSavePayload> = {}): SessionSavePayload => ({
      session_id: overrides.session_id ?? sessionId,
      job_id: overrides.job_id ?? jobId,
      current_step: overrides.current_step ?? currentStep,
      tops: overrides.tops ?? tops,
      transcript: overrides.transcript ?? transcript,
      assignments: overrides.assignments ?? assignments,
      speaker_names: overrides.speaker_names ?? speakerNames,
      summaries: overrides.summaries ?? summaries,
      summary_reviews: overrides.summary_reviews ?? summaryReviews,
      export_metadata: overrides.export_metadata ?? exportMetadata,
      skipped_assignment: overrides.skipped_assignment ?? skippedAssignment,
    }),
    [
      assignments,
      currentStep,
      exportMetadata,
      jobId,
      sessionId,
      skippedAssignment,
      speakerNames,
      summaryReviews,
      summaries,
      tops,
      transcript,
    ]
  );

  const applySession = useCallback((session: SessionResponse | SessionDraft) => {
    setSessionId(session.session_id ?? null);
    setJobId(session.job_id ?? null);
    setCurrentStep(session.current_step ?? 1);
    setTops(session.tops?.length ? session.tops : EMPTY_TOPS);
    setTranscript(session.transcript ?? []);
    setAssignments(session.assignments ?? []);
    setAgendaDetection(null);
    setAgendaDetectionError(null);
    setSpeakerNames(session.speaker_names ?? {});
    setSkipAgendaDetection(Boolean(session.skipped_assignment));
    setSummaries(normalizeSummaries(session.summaries));
    setSummaryReviews(normalizeSummaryReviews(session.summary_reviews));
    setSummaryInputFingerprint(getSessionSummaryInputFingerprint(session));
    setExportMetadata({
      ...DEFAULT_EXPORT_METADATA,
      ...(session.export_metadata ?? {}),
    });
    setSkippedAssignment(Boolean(session.skipped_assignment));
    setAudioUrl(withApiBase(session.audio_url));
    setAudioFile(null);
    setPdfFile(null);
    setAutoDetectTopsFromPdf(false);
    setPipelineId((session as SessionDraft).pipeline_id ?? null);
    setPipelineJob(null);
    setPipelineNotice(null);
    setDirectProtocolAvailable(false);
    setIsProcessing(false);
    setProcessingError(null);
    setProcessingStatus("");
    setProcessingProgress(0);
  }, []);

  const applyPipelineResult = useCallback((result: PipelineResultResponse) => {
    applySession(result.session);
    const completedPipeline = result.pipeline;
    const warnings = result.warnings ?? completedPipeline.warnings ?? [];
    const agendaDetectionResult = result.agenda_detection ?? null;
    const needsReview = hasReviewUncertainty(
      result.session,
      warnings,
      agendaDetectionResult
    );

    setPipelineJob(completedPipeline);
    setPipelineId(null);
    setJobId(result.session.job_id ?? result.job?.job_id ?? completedPipeline.transcription_job_id ?? null);
    setAgendaDetection(agendaDetectionResult);
    setAgendaDetectionError(null);
    setSummaryReviews(
      normalizeSummaryReviews(result.summary_reviews ?? result.session.summary_reviews)
    );
    setSummaryInputFingerprint(
      buildSummaryInputFingerprint(
        result.session.tops ?? [],
        result.session.transcript ?? [],
        result.session.assignments ?? [],
        result.session.speaker_names ?? {}
      )
    );
    setIsProcessing(false);
    setProcessingProgress(100);
    setProcessingStatus("Pipeline abgeschlossen");
    setProcessingError(null);
    setDirectProtocolAvailable(!needsReview);
    setPipelineNotice(
      needsReview
        ? "Automatische Verarbeitung abgeschlossen. Bitte prüfen Sie unsichere Zuordnungen vor dem Protokoll."
        : "Automatische Verarbeitung abgeschlossen. Sie können direkt zum Protokoll wechseln oder die Zuordnung prüfen."
    );
    setCurrentStep(2);

    try {
      localStorage.removeItem(ACTIVE_PIPELINE_KEY);
    } catch (e) {
      console.error("Failed to clear active pipeline id:", e);
    }
  }, [applySession]);

  const resetSession = useCallback(() => {
    if (saveTimerRef.current !== null) {
      window.clearTimeout(saveTimerRef.current);
      saveTimerRef.current = null;
    }
    setSessionId(null);
    setJobId(null);
    setPipelineId(null);
    setPipelineJob(null);
    setPipelineNotice(null);
    setDirectProtocolAvailable(false);
    setCurrentStep(1);
    setAudioFile(null);
    setPdfFile(null);
    setTops(EMPTY_TOPS);
    setTranscript([]);
    setAssignments([]);
    setAgendaDetection(null);
    setAgendaDetectionError(null);
    setSummaries({});
    setSummaryReviews({});
    setSummaryInputFingerprint(null);
    setExportMetadata(DEFAULT_EXPORT_METADATA);
    setAudioUrl(null);
    setSpeakerNames({});
    setSkippedAssignment(false);
    setSkipAgendaDetection(false);
    setAutoDetectTopsFromPdf(false);
    setIsGeneratingSummary(false);
    setIsProcessing(false);
    setProcessingError(null);
    setProcessingStatus("");
    setProcessingProgress(0);
    setSessionMessage(null);
    setRestoreCandidate(null);
    lastSavedPayloadRef.current = null;
    try {
      localStorage.removeItem(ACTIVE_SESSION_KEY);
      localStorage.removeItem(ACTIVE_PIPELINE_KEY);
      localStorage.removeItem(SESSION_DRAFT_KEY);
    } catch (e) {
      console.error("Failed to clear session draft:", e);
    }
  }, []);

  // Check backend availability on mount
  useEffect(() => {
    checkBackendHealth().then(setBackendAvailable);
  }, []);

  // Detect restorable session on mount. Backend remains the source of truth.
  useEffect(() => {
    try {
      const storedSessionId = localStorage.getItem(ACTIVE_SESSION_KEY);
      const storedPipelineId = localStorage.getItem(ACTIVE_PIPELINE_KEY);
      const storedDraft = localStorage.getItem(SESSION_DRAFT_KEY);
      const draft = storedDraft ? (JSON.parse(storedDraft) as SessionDraft) : null;

      if (storedSessionId || storedPipelineId || draft?.session_id || draft?.pipeline_id) {
        setRestoreCandidate({
          sessionId: storedSessionId || draft?.session_id || null,
          pipelineId: storedPipelineId || draft?.pipeline_id || null,
          draft,
        });
      }
    } catch (e) {
      console.error("Failed to read session draft:", e);
    }
  }, []);

  // Save LLM settings to localStorage when they change
  useEffect(() => {
    try {
      localStorage.setItem(LLM_SETTINGS_KEY, JSON.stringify(llmSettings));
    } catch (e) {
      console.error("Failed to save LLM settings to localStorage:", e);
    }
  }, [llmSettings]);

  useEffect(() => {
    try {
      localStorage.setItem(SPEAKER_MEMORY_OPT_IN_KEY, String(rememberSpeakers));
    } catch (e) {
      console.error("Failed to save speaker memory setting:", e);
    }
  }, [rememberSpeakers]);

  useEffect(() => {
    if (saveTimerRef.current !== null) {
      window.clearTimeout(saveTimerRef.current);
    }

    if (!sessionId) {
      return;
    }

    const payload = buildSessionPayload();
    const draft: SessionDraft = {
      ...payload,
      session_id: sessionId,
      audio_url: audioUrl,
      pipeline_id: pipelineId,
      summary_input_fingerprint: summaryInputFingerprint,
    };

    try {
      localStorage.setItem(ACTIVE_SESSION_KEY, sessionId);
      if (pipelineId) {
        localStorage.setItem(ACTIVE_PIPELINE_KEY, pipelineId);
      } else {
        localStorage.removeItem(ACTIVE_PIPELINE_KEY);
      }
      localStorage.setItem(SESSION_DRAFT_KEY, JSON.stringify(draft));
    } catch (e) {
      console.error("Failed to save local session draft:", e);
    }

    const serializedPayload = JSON.stringify({ ...payload, session_id: sessionId });
    if (serializedPayload === lastSavedPayloadRef.current) {
      return;
    }

    saveTimerRef.current = window.setTimeout(() => {
      saveSession({ ...payload, session_id: sessionId })
        .then((savedSession) => {
          lastSavedPayloadRef.current = serializedPayload;
          setSessionId(savedSession.session_id);
          setSessionMessage(null);
        })
        .catch((error) => {
          const message =
            error instanceof Error ? error.message : "Sitzung konnte nicht gespeichert werden";
          setSessionMessage(message);
          console.error("Failed to save session:", error);
        });
    }, 500);

    return () => {
      if (saveTimerRef.current !== null) {
        window.clearTimeout(saveTimerRef.current);
      }
    };
  }, [audioUrl, buildSessionPayload, pipelineId, sessionId, summaryInputFingerprint]);

  const updatePipelineProcessingState = (status: PipelineJob) => {
    setPipelineJob(status);
    setPipelineId(status.pipeline_id);
    setJobId(status.transcription_job_id ?? null);
    if (status.session_id) {
      setSessionId(status.session_id);
    }
    setProcessingProgress(status.progress);
    setProcessingStatus("");
  };

  const pollPipelineToResult = async (
    activePipelineId: string,
    initialStatus?: PipelineJob
  ) => {
    setIsProcessing(true);
    setProcessingError(null);
    if (initialStatus) {
      updatePipelineProcessingState(initialStatus);
    }

    const completedStatus =
      initialStatus?.status === "completed"
        ? initialStatus
        : await pollPipeline(activePipelineId, updatePipelineProcessingState);
    updatePipelineProcessingState(completedStatus);
    const result = await getPipelineResult(activePipelineId);
    applyPipelineResult(result);
  };

  // Legacy transcription flow used as a fallback when the pipeline endpoint fails.
  const startLegacyTranscription = async (fallbackReason?: string) => {
    if (!audioFile) return;

    setIsProcessing(true);
    setProcessingProgress(0);
    setProcessingStatus(
      fallbackReason
        ? `Pipeline nicht verfügbar (${fallbackReason}). Klassischer Workflow wird gestartet...`
        : "Audio wird hochgeladen..."
    );
    setProcessingError(null);
    setPipelineId(null);
    setPipelineJob(null);

    try {
      const submittedTops = skipAgendaDetection
        ? []
        : tops.map((top) => top.trim()).filter(Boolean);
      const preparedSession = await saveSession(
        buildSessionPayload({
          current_step: 1,
          transcript: [],
          assignments: [],
          tops: submittedTops,
          summaries: {},
          summary_reviews: {},
        })
      );
      const activeSessionId = preparedSession.session_id;
      setSessionId(activeSessionId);
      try {
        localStorage.setItem(ACTIVE_SESSION_KEY, activeSessionId);
      } catch (e) {
        console.error("Failed to save active session id:", e);
      }

      // Start transcription job
      const job = await apiStartTranscription(
        audioFile,
        activeSessionId,
        rememberSpeakers
      );

      setJobId(job.job_id);

      // Poll for completion
      const completedJob = await pollTranscription(
        job.job_id,
        (progress, message) => {
          setProcessingProgress(progress);
          setProcessingStatus(message);
        }
      );

      // Set transcript and audio URL
      const transcriptResult = completedJob.transcript ?? [];
      setTranscript(transcriptResult);
      setSummaryReviews({});
      setSummaries({});
      setSummaryInputFingerprint(null);
      setAgendaDetection(null);
      setAgendaDetectionError(null);

      // Set audio URL for playback (use relative URL to go through nginx proxy)
      if (completedJob.audio_url) {
        setAudioUrl(withApiBase(completedJob.audio_url));
      }

      const knownTops = submittedTops;
      if (skipAgendaDetection) {
        setSkippedAssignment(true);
        setTops([]);
        setAssignments(new Array(transcriptResult.length).fill(null));
        setIsProcessing(false);
        setCurrentStep(2);
        return;
      }
      setProcessingStatus("TOPs und Segmentgrenzen werden erkannt...");

      try {
        const detected = await detectAgenda({
          tops: knownTops,
          transcript: transcriptResult,
          model: llmSettings.model,
        });
        const detectionTranscript =
          detected.transcript && detected.transcript.length > 0
            ? detected.transcript
            : transcriptResult;
        const detectedTops = detected.tops.map((top) => top.trim()).filter(Boolean);
        const detectedAssignments =
          detected.assignments.length === detectionTranscript.length
            ? detected.assignments
            : new Array(detectionTranscript.length).fill(null);

        if (detectedTops.length > 0) {
          setTranscript(detectionTranscript);
          setTops(detectedTops);
          setAssignments(detectedAssignments);
          setAgendaDetection({
            ...detected,
            tops: detectedTops,
            transcript: detectionTranscript,
            assignments: detectedAssignments,
          });
          setSkippedAssignment(false);
          setIsProcessing(false);
          setCurrentStep(2);
        } else {
          setTranscript(detectionTranscript);
          setSkippedAssignment(true);
          setTops([]);
          setAssignments(new Array(detectionTranscript.length).fill(null));
          setIsProcessing(false);
          setCurrentStep(2);
        }
      } catch (error) {
        const detectionError =
          error instanceof Error
            ? error.message
            : "Automatische TOP-Erkennung fehlgeschlagen";
        console.warn("Agenda detection failed:", error);
        setAgendaDetectionError(detectionError);
        setAgendaDetection(null);
        setIsProcessing(false);

        if (knownTops.length > 0) {
          setSkippedAssignment(false);
          setTops(knownTops);
          setAssignments(new Array(transcriptResult.length).fill(null));
          setCurrentStep(2);
        } else {
          setSkippedAssignment(true);
          setTops([]);
          setAssignments(new Array(transcriptResult.length).fill(null));
          setCurrentStep(2);
        }
      }
    } catch (error) {
      const errorMessage =
        error instanceof Error ? error.message : "Unbekannter Fehler";
      setProcessingError(errorMessage);
      setProcessingStatus(`Fehler: ${errorMessage}`);
      // Keep processing screen to show error
    }
  };

  const handleRestoreSession = async () => {
    if (!restoreCandidate) return;

    setIsRestoringSession(true);
    setSessionMessage(null);

    try {
      if (restoreCandidate.pipelineId) {
        const status = await getPipelineStatus(restoreCandidate.pipelineId);
        setRestoreCandidate(null);
        if (status.session_id) {
          setSessionId(status.session_id);
          localStorage.setItem(ACTIVE_SESSION_KEY, status.session_id);
        }
        setPipelineJob(status);
        if (status.status === "completed") {
          setPipelineId(status.pipeline_id);
          const result = await getPipelineResult(status.pipeline_id);
          applyPipelineResult(result);
        } else if (status.status === "failed" || status.status === "cancelled") {
          try {
            localStorage.removeItem(ACTIVE_PIPELINE_KEY);
          } catch (e) {
            console.error("Failed to clear active pipeline id:", e);
          }
          setPipelineId(null);
          setDirectProtocolAvailable(false);
          const inactiveMessage = status.error || "Pipeline ist nicht mehr aktiv";
          const fallbackSessionId = status.session_id ?? restoreCandidate.sessionId;
          if (fallbackSessionId) {
            try {
              const restored = await loadSession(fallbackSessionId);
              applySession(restored);
              setPipelineId(null);
              setSessionMessage(`${inactiveMessage}. Gespeicherte Sitzung wurde geladen.`);
            } catch (loadError) {
              if (restoreCandidate.draft) {
                applySession({ ...restoreCandidate.draft, pipeline_id: null });
                setPipelineId(null);
                setSessionMessage(`${inactiveMessage}. Lokaler Draft wurde geladen.`);
              } else {
                const message =
                  loadError instanceof Error ? loadError.message : inactiveMessage;
                setSessionMessage(message);
              }
            }
          } else if (restoreCandidate.draft) {
            applySession({ ...restoreCandidate.draft, pipeline_id: null });
            setPipelineId(null);
            setSessionMessage(`${inactiveMessage}. Lokaler Draft wurde geladen.`);
          } else {
            setSessionMessage(inactiveMessage);
          }
        } else {
          setPipelineId(status.pipeline_id);
          void pollPipelineToResult(status.pipeline_id, status).catch((error) => {
            const message =
              error instanceof Error ? error.message : "Pipeline konnte nicht fortgesetzt werden";
            setProcessingError(message);
            setProcessingStatus(`Fehler: ${message}`);
            setIsProcessing(true);
          });
        }
      } else if (restoreCandidate.sessionId) {
        const restored = await loadSession(restoreCandidate.sessionId);
        applySession(restored);
        if ((restored.current_step ?? 1) === 2 && hasAnySummary(restored)) {
          const canGoDirect =
            hasFreshSessionSummaries(restored) && !hasReviewUncertainty(restored);
          setDirectProtocolAvailable(canGoDirect);
          setPipelineNotice(
            canGoDirect
              ? "Automatische Verarbeitung abgeschlossen. Sie können direkt zum Protokoll wechseln oder die Zuordnung prüfen."
              : "Automatische Verarbeitung abgeschlossen. Bitte prüfen Sie unsichere Zuordnungen vor dem Protokoll."
          );
        }
        lastSavedPayloadRef.current = JSON.stringify(
          buildSessionPayload({
            session_id: restored.session_id,
            job_id: restored.job_id,
            current_step: restored.current_step,
            tops: restored.tops,
            transcript: restored.transcript ?? [],
            assignments: restored.assignments,
            speaker_names: restored.speaker_names,
            summaries: normalizeSummaries(restored.summaries),
            summary_reviews: normalizeSummaryReviews(restored.summary_reviews),
            skipped_assignment: restored.skipped_assignment,
          })
        );
      } else if (restoreCandidate.draft) {
        applySession(restoreCandidate.draft);
        if ((restoreCandidate.draft.current_step ?? 1) === 2 && hasAnySummary(restoreCandidate.draft)) {
          const draftSession = restoreCandidate.draft as SessionResponse;
          const canGoDirect =
            hasFreshSessionSummaries(restoreCandidate.draft) &&
            !hasReviewUncertainty(draftSession);
          setDirectProtocolAvailable(canGoDirect);
          setPipelineNotice(
            canGoDirect
              ? "Automatische Verarbeitung abgeschlossen. Sie können direkt zum Protokoll wechseln oder die Zuordnung prüfen."
              : "Automatische Verarbeitung abgeschlossen. Bitte prüfen Sie unsichere Zuordnungen vor dem Protokoll."
          );
        }
      }
      setRestoreCandidate(null);
    } catch (error) {
      if (restoreCandidate.draft) {
        applySession(restoreCandidate.draft);
        setRestoreCandidate(null);
        setSessionMessage("Backend-Sitzung nicht verfügbar. Lokaler Draft wurde geladen.");
      } else {
        const message =
          error instanceof Error ? error.message : "Sitzung konnte nicht geladen werden";
        setSessionMessage(message);
      }
    } finally {
      setIsRestoringSession(false);
    }
  };

  // Start end-to-end pipeline via backend API
  const startPipeline = async () => {
    if (!audioFile) return;

    setIsProcessing(true);
    setProcessingProgress(0);
    setProcessingStatus("Audio wird hochgeladen und Pipeline gestartet...");
    setProcessingError(null);
    setPipelineNotice(null);
    setDirectProtocolAvailable(false);

    try {
      const submittedTops = skipAgendaDetection
        ? []
        : tops.map((top) => top.trim()).filter(Boolean);
      const shouldAutoDetectTopsFromPdf =
        autoDetectTopsFromPdf && submittedTops.length === 0;
      const preparedSession = await saveSession(
        buildSessionPayload({
          current_step: 1,
          transcript: [],
          assignments: [],
          tops: submittedTops,
          summaries: {},
          summary_reviews: {},
          skipped_assignment: skipAgendaDetection,
        })
      );
      const activeSessionId = preparedSession.session_id;
      setSessionId(activeSessionId);
      localStorage.setItem(ACTIVE_SESSION_KEY, activeSessionId);

      const pipeline = await apiStartPipeline(audioFile, {
        sessionId: activeSessionId,
        tops: submittedTops,
        pdfFile,
        autoDetectTopsFromPdf: shouldAutoDetectTopsFromPdf,
        model: llmSettings.model,
        systemPrompt: llmSettings.systemPrompt,
        rememberSpeakers,
        skipAgendaDetection,
      });
      updatePipelineProcessingState(pipeline);
      localStorage.setItem(ACTIVE_PIPELINE_KEY, pipeline.pipeline_id);
      await pollPipelineToResult(pipeline.pipeline_id, pipeline);
    } catch (error) {
      const errorMessage =
        error instanceof Error ? error.message : "Unbekannter Fehler";
      console.warn("Pipeline failed, falling back to legacy flow:", error);
      try {
        localStorage.removeItem(ACTIVE_PIPELINE_KEY);
      } catch (storageError) {
        console.error("Failed to clear active pipeline id:", storageError);
      }
      if (/abgebrochen|cancel/i.test(errorMessage)) {
        setProcessingError(errorMessage);
        setProcessingStatus(`Fehler: ${errorMessage}`);
        return;
      }
      await startLegacyTranscription(errorMessage);
    }
  };

  const handleStartNewSession = () => {
    resetSession();
  };

  // Generate summary for entire conversation (when no TOPs are defined)
  const generateSummaryForAll = async (
    transcriptLines: TranscriptLine[],
    noTopMode = false
  ) => {
    setIsGeneratingSummary(true);
    setSummaries({ 0: "Zusammenfassung wird generiert..." });
    setSummaryReviews({});
    setSummaryInputFingerprint(null);

    try {
      const result = await generateSummary(
        DEFAULT_TOP_TITLE,
        applySpeakerNames(transcriptLines),
        {
          model: llmSettings.model,
          systemPrompt: GENERIC_SUMMARY_PROMPT,
        }
      );
      setSummaries({ 0: result.summary });
      setSummaryReviews({
        0: {
          structured: result.structured ?? null,
          source_links: result.sourceLinks,
          review_warnings: result.reviewWarnings,
          fallback_used: result.fallbackUsed,
          chunks_processed: result.chunksProcessed,
        },
      });
      setSummaryInputFingerprint(
        buildSummaryInputFingerprint(
          noTopMode ? [] : [DEFAULT_TOP_TITLE],
          transcriptLines,
          new Array(transcriptLines.length).fill(noTopMode ? null : 0),
          speakerNames
        )
      );

    } catch (error) {
      const errorMessage =
        error instanceof Error ? error.message : "Unbekannter Fehler";
      setSummaries({ 0: `Fehler: ${errorMessage}` });
      setSummaryReviews({});
      setSummaryInputFingerprint(null);
    } finally {
      setIsGeneratingSummary(false);
    }
  };

  // Handle step navigation
  const handleStep1Next = () => {
    if (backendAvailable === false) {
      // Show error - backend not available
      alert("Backend nicht erreichbar.");
      return;
    }
    startPipeline();
  };

  const handleStep2Next = async () => {
    // Move to step 3 first
    setCurrentStep(3);

    let totalDuration = 0;

    // Generate summaries for each TOP with assigned lines
    const validTops = tops.filter((t) => t.trim() !== "");
    if (validTops.length === 0) {
      setSkippedAssignment(true);
      setAssignments(new Array(transcript.length).fill(null));
      await generateSummaryForAll(transcript, true);
      setDirectProtocolAvailable(false);
      setPipelineNotice(null);
      return;
    }
    const hasCurrentSummaries = summariesAreFresh && validTops.some((_, index) => {
      const summary = summaries[index];
      return typeof summary === "string" && summary.trim() !== "";
    });

    if (hasCurrentSummaries) {
      setDirectProtocolAvailable(false);
      setPipelineNotice(null);
      return;
    }

    const generationFingerprint = currentSummaryInputFingerprint;
    const newSummaries: Record<number, string> = {};
    const newSummaryReviews: Record<number, SummaryReview> = {};
    setSummaryInputFingerprint(null);

    console.log(`[Summary] Starting generation for ${validTops.length} TOPs`);

    for (let index = 0; index < validTops.length; index++) {
      const topLines = transcript.filter((_, i) => assignments[i] === index);
      console.log(
        `[Summary] TOP ${index + 1}: ${topLines.length} lines assigned`
      );

      if (topLines.length > 0) {
        // Set placeholder while generating
        newSummaries[index] = "Zusammenfassung wird generiert...";
        setSummaries({ ...newSummaries });
        setSummaryReviews({ ...newSummaryReviews });

        try {
          console.log(`[Summary] Generating summary for TOP ${index + 1}...`);
          const result = await generateSummary(
            validTops[index]!,
            applySpeakerNames(topLines),
            {
              model: llmSettings.model,
              systemPrompt: llmSettings.systemPrompt,
            }
          );
          console.log(
            `[Summary] TOP ${index + 1} complete, length: ${
              result.summary.length
            }, duration: ${result.durationSeconds}s`
          );
          newSummaries[index] = result.summary;
          newSummaryReviews[index] = {
            structured: result.structured ?? null,
            source_links: result.sourceLinks,
            review_warnings: result.reviewWarnings,
            fallback_used: result.fallbackUsed,
            chunks_processed: result.chunksProcessed,
          };
          totalDuration += result.durationSeconds;
          setSummaries({ ...newSummaries });
          setSummaryReviews({ ...newSummaryReviews });
        } catch (error) {
          console.error(`[Summary] TOP ${index + 1} failed:`, error);
          const errorMessage =
            error instanceof Error ? error.message : "Unbekannter Fehler";
          newSummaries[index] = `Fehler: ${errorMessage}`;
          delete newSummaryReviews[index];
          setSummaries({ ...newSummaries });
          setSummaryReviews({ ...newSummaryReviews });
        }
      } else {
        console.log(`[Summary] TOP ${index + 1}: skipped (no lines)`);
      }
    }

    console.log(
      `[Summary] All TOPs processed, total duration: ${totalDuration}s`
    );
    setSummaryInputFingerprint(generationFingerprint);

  };

  const handleStep2Back = () => {
    setDirectProtocolAvailable(false);
    setPipelineNotice(null);
    setCurrentStep(1);
    setTranscript([]);
    setAssignments([]);
    setAgendaDetection(null);
    setAgendaDetectionError(null);
    setProcessingError(null);
    setAudioUrl(null);
  };

  const handleStep3Back = () => {
    setDirectProtocolAvailable(false);
    setCurrentStep(2);
  };

  const handleRegenerateSummary = async (topIndex: number) => {
    setIsGeneratingSummary(true);

    const validTops = tops.filter((t) => t.trim() !== "");
    const noTopMode = validTops.length === 0;
    const topLines = noTopMode
      ? transcript
      : transcript.filter((_, i) => assignments[i] === topIndex);
    const prompt = skippedAssignment
      ? GENERIC_SUMMARY_PROMPT
      : llmSettings.systemPrompt;

    try {
      const result = await generateSummary(
        noTopMode ? DEFAULT_TOP_TITLE : validTops[topIndex]!,
        applySpeakerNames(topLines),
        {
          model: llmSettings.model,
          systemPrompt: prompt,
        }
      );
      setSummaries((prev) => ({ ...prev, [topIndex]: result.summary }));
      setSummaryReviews((prev) => ({
        ...prev,
        [topIndex]: {
          structured: result.structured ?? null,
          source_links: result.sourceLinks,
          review_warnings: result.reviewWarnings,
          fallback_used: result.fallbackUsed,
          chunks_processed: result.chunksProcessed,
        },
      }));
    } catch (error) {
      const errorMessage =
        error instanceof Error ? error.message : "Unbekannter Fehler";
      setSummaries((prev) => ({
        ...prev,
        [topIndex]: `Fehler: ${errorMessage}`,
      }));
      setSummaryReviews((prev) => {
        const next = { ...prev };
        delete next[topIndex];
        return next;
      });
    }

    setIsGeneratingSummary(false);
  };

  const handleRetry = () => {
    setProcessingError(null);
    setIsProcessing(false);
  };

  const handleDirectProtocol = () => {
    if (!hasFreshSummariesInState) {
      setDirectProtocolAvailable(false);
      setPipelineNotice("Zusammenfassungen müssen nach den Korrekturen neu erstellt werden.");
      return;
    }
    setCurrentStep(3);
    setDirectProtocolAvailable(false);
    setPipelineNotice(null);
  };

  const handleCancelPipeline = async () => {
    if (!pipelineId) return;

    try {
      const cancelled = await cancelPipeline(pipelineId);
      setPipelineJob(cancelled);
      setProcessingProgress(cancelled.progress);
      setProcessingStatus("Pipeline abgebrochen");
      setProcessingError("Pipeline abgebrochen");
      setIsProcessing(false);
      setPipelineId(null);
      localStorage.removeItem(ACTIVE_PIPELINE_KEY);
    } catch (error) {
      const message =
        error instanceof Error ? error.message : "Pipeline konnte nicht abgebrochen werden";
      setProcessingError(message);
      setProcessingStatus(`Fehler: ${message}`);
    }
  };

  // Filter out empty TOPs for display
  const validTops = tops.filter((t) => t.trim() !== "");

  return (
    <Layout onSettingsClick={() => setIsSettingsOpen(true)}>
      {/* LLM Settings Panel */}
      <LLMSettingsPanel
        isOpen={isSettingsOpen}
        onClose={() => setIsSettingsOpen(false)}
        settings={llmSettings}
        onSettingsChange={setLlmSettings}
      />

      {/* Backend status indicator */}
      {backendAvailable === false && (
        <div className="mb-4 p-3 bg-yellow-50 border border-yellow-200 rounded-lg text-sm text-yellow-800">
          Backend nicht erreichbar.
        </div>
      )}

      {(restoreCandidate || sessionId || sessionMessage) && (
        <div className="mb-4 bg-white border border-gray-200 rounded-lg p-3 flex flex-wrap items-center justify-between gap-3">
          <div>
            <div className="text-sm font-medium text-gray-800">
              {sessionId ? "Sitzung aktiv" : "Letzte Sitzung verfügbar"}
            </div>
            {sessionMessage && (
              <div className="text-sm text-yellow-700 mt-1">{sessionMessage}</div>
            )}
          </div>
          <div className="flex gap-2">
            {restoreCandidate && (
              <button
                type="button"
                onClick={handleRestoreSession}
                disabled={isRestoringSession}
                className="px-4 py-2 bg-blue-600 text-white rounded-lg hover:bg-blue-700 disabled:opacity-50"
              >
                {isRestoringSession ? "Wird geladen..." : "Letzte Sitzung fortsetzen"}
              </button>
            )}
            <button
              type="button"
              onClick={handleStartNewSession}
              className="px-4 py-2 bg-gray-100 text-gray-700 rounded-lg hover:bg-gray-200"
            >
              Neue Sitzung starten
            </button>
          </div>
        </div>
      )}

      {!isProcessing && <StepIndicator currentStep={currentStep} />}

      {!isProcessing && pipelineNotice && (
        <div className="mb-4 rounded-lg border border-blue-200 bg-blue-50 p-3 text-sm text-blue-900">
          <div className="flex flex-wrap items-center justify-between gap-3">
            <span>{pipelineNotice}</span>
            {directProtocolAvailable && hasFreshSummariesInState && (
              <button
                type="button"
                onClick={handleDirectProtocol}
                className="rounded-lg bg-blue-600 px-4 py-2 font-medium text-white hover:bg-blue-700"
              >
                Direkt zum Protokoll
              </button>
            )}
          </div>
        </div>
      )}

      {isProcessing ? (
        <div>
          <ProcessingStep
            progress={processingProgress}
            status={processingStatus}
            pipeline={pipelineJob}
            canCancel={Boolean(pipelineId)}
            onCancel={handleCancelPipeline}
          />
          {processingError && (
            <div className="mt-4 text-center">
              <p className="text-red-600 mb-4">{processingError}</p>
              <button
                onClick={handleRetry}
                className="px-4 py-2 bg-blue-600 text-white rounded-lg hover:bg-blue-700"
              >
                Erneut versuchen
              </button>
            </div>
          )}
        </div>
      ) : currentStep === 1 ? (
        <UploadStep
          onNext={handleStep1Next}
          audioFile={audioFile}
          setAudioFile={setAudioFile}
          pdfFile={pdfFile}
          setPdfFile={setPdfFile}
          tops={tops}
          setTops={setTops}
          llmSettings={llmSettings}
          rememberSpeakers={rememberSpeakers}
          setRememberSpeakers={setRememberSpeakers}
          skipAgendaDetection={skipAgendaDetection}
          setSkipAgendaDetection={setSkipAgendaDetection}
          autoDetectTopsFromPdf={autoDetectTopsFromPdf}
          setAutoDetectTopsFromPdf={setAutoDetectTopsFromPdf}
        />
      ) : currentStep === 2 ? (
        <AssignmentStep
          onNext={handleStep2Next}
          onBack={handleStep2Back}
          tops={validTops}
          setTops={setTops}
          transcript={transcript}
          setTranscript={setTranscript}
          assignments={assignments}
          setAssignments={setAssignments}
          agendaDetection={agendaDetection}
          agendaDetectionError={agendaDetectionError}
          audioUrl={audioUrl ?? undefined}
          speakerNames={speakerNames}
          setSpeakerNames={setSpeakerNames}
          sessionId={sessionId}
          hasSummaries={hasFreshSummariesInState}
          rememberSpeakers={rememberSpeakers}
        />
      ) : (
        <SummaryStep
          onBack={handleStep3Back}
          tops={validTops}
          transcript={transcript}
          assignments={assignments}
          summaries={summariesAreFresh ? summaries : {}}
          setSummaries={setSummaries}
          summaryReviews={summariesAreFresh ? summaryReviews : {}}
          onRegenerateSummary={handleRegenerateSummary}
          isGenerating={isGeneratingSummary}
          audioUrl={audioUrl ?? undefined}
          speakerNames={speakerNames}
          exportMetadata={exportMetadata}
          setExportMetadata={setExportMetadata}
        />
      )}
    </Layout>
  );
}

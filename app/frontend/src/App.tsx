import { useCallback, useEffect, useRef, useState } from "react";
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
  generateSummary,
  detectAgenda,
  checkBackendHealth,
  reportSessionComplete,
  saveSession,
  loadSession,
} from "./api";
import type {
  AgendaDetectionResponse,
  ExportMetadata,
  SessionResponse,
  SessionSavePayload,
  SummaryReview,
  TranscriptLine,
} from "./types";

// LocalStorage key for LLM settings
const LLM_SETTINGS_KEY = "llm-settings";
const ACTIVE_SESSION_KEY = "active-session-id";
const SESSION_DRAFT_KEY = "active-session-draft";
const TELEMETRY_OPT_IN_KEY = "telemetry-opt-in";

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
}

function withApiBase(url?: string | null): string | null {
  if (!url) return null;
  if (/^https?:\/\//i.test(url)) return url;
  const baseUrl = import.meta.env.VITE_API_URL || "";
  return `${baseUrl}${url}`;
}

function getSystemPromptKind(
  prompt: string,
  genericPrompt: string
): "default" | "custom" | "generic" {
  if (prompt === genericPrompt) return "generic";
  if (prompt === DEFAULT_LLM_SETTINGS.systemPrompt) return "default";
  return "custom";
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
  const [tops, setTops] = useState<string[]>(EMPTY_TOPS);
  const [transcript, setTranscript] = useState<TranscriptLine[]>([]);
  const [assignments, setAssignments] = useState<(number | null)[]>([]);
  const [agendaDetection, setAgendaDetection] = useState<AgendaDetectionResponse | null>(null);
  const [agendaDetectionError, setAgendaDetectionError] = useState<string | null>(null);
  const [summaries, setSummaries] = useState<Record<number, string>>({});
  const [summaryReviews, setSummaryReviews] = useState<Record<number, SummaryReview>>({});
  const [exportMetadata, setExportMetadata] = useState<ExportMetadata>(DEFAULT_EXPORT_METADATA);
  const [isGeneratingSummary, setIsGeneratingSummary] = useState(false);
  const [audioUrl, setAudioUrl] = useState<string | null>(null);
  const [speakerNames, setSpeakerNames] = useState<Record<string, string>>({});
  const [skippedAssignment, setSkippedAssignment] = useState(false);
  const [sessionId, setSessionId] = useState<string | null>(null);
  const [restoreCandidate, setRestoreCandidate] = useState<{
    sessionId: string | null;
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

  // Telemetry state
  const [jobId, setJobId] = useState<string | null>(null);
  const [telemetryOptIn, setTelemetryOptIn] = useState<boolean>(() => {
    try {
      return localStorage.getItem(TELEMETRY_OPT_IN_KEY) === "true";
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
    setSummaries(normalizeSummaries(session.summaries));
    setSummaryReviews(normalizeSummaryReviews(session.summary_reviews));
    setExportMetadata({
      ...DEFAULT_EXPORT_METADATA,
      ...(session.export_metadata ?? {}),
    });
    setSkippedAssignment(Boolean(session.skipped_assignment));
    setAudioUrl(withApiBase(session.audio_url));
    setAudioFile(null);
    setIsProcessing(false);
    setProcessingError(null);
    setProcessingStatus("");
    setProcessingProgress(0);
  }, []);

  const resetSession = useCallback(() => {
    if (saveTimerRef.current !== null) {
      window.clearTimeout(saveTimerRef.current);
      saveTimerRef.current = null;
    }
    setSessionId(null);
    setJobId(null);
    setCurrentStep(1);
    setAudioFile(null);
    setTops(EMPTY_TOPS);
    setTranscript([]);
    setAssignments([]);
    setAgendaDetection(null);
    setAgendaDetectionError(null);
    setSummaries({});
    setSummaryReviews({});
    setExportMetadata(DEFAULT_EXPORT_METADATA);
    setAudioUrl(null);
    setSpeakerNames({});
    setSkippedAssignment(false);
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
      const storedDraft = localStorage.getItem(SESSION_DRAFT_KEY);
      const draft = storedDraft ? (JSON.parse(storedDraft) as SessionDraft) : null;

      if (storedSessionId || draft?.session_id) {
        setRestoreCandidate({
          sessionId: storedSessionId || draft?.session_id || null,
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
      localStorage.setItem(TELEMETRY_OPT_IN_KEY, String(telemetryOptIn));
    } catch (e) {
      console.error("Failed to save telemetry setting:", e);
    }
  }, [telemetryOptIn]);

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
    };

    try {
      localStorage.setItem(ACTIVE_SESSION_KEY, sessionId);
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
  }, [audioUrl, buildSessionPayload, sessionId]);

  // Start transcription via backend API
  const startTranscription = async () => {
    if (!audioFile) return;

    setIsProcessing(true);
    setProcessingProgress(0);
    setProcessingStatus("Audio wird hochgeladen...");
    setProcessingError(null);

    try {
      const preparedSession = await saveSession(
        buildSessionPayload({
          current_step: 1,
          transcript: [],
          assignments: [],
          tops: tops,
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
      const job = await apiStartTranscription(audioFile, activeSessionId);

      // Store job ID for telemetry
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
      setAgendaDetection(null);
      setAgendaDetectionError(null);

      // Set audio URL for playback (use relative URL to go through nginx proxy)
      if (completedJob.audio_url) {
        setAudioUrl(withApiBase(completedJob.audio_url));
      }

      const knownTops = tops.map((top) => top.trim()).filter(Boolean);
      setProcessingStatus("TOPs und Segmentgrenzen werden erkannt...");

      try {
        const detected = await detectAgenda({
          tops: knownTops,
          transcript: transcriptResult,
          model: llmSettings.model,
        });
        const detectedTops = detected.tops.map((top) => top.trim()).filter(Boolean);
        const detectedAssignments =
          detected.assignments.length === transcriptResult.length
            ? detected.assignments
            : new Array(transcriptResult.length).fill(null);

        if (detectedTops.length > 0) {
          setTops(detectedTops);
          setAssignments(detectedAssignments);
          setAgendaDetection({
            ...detected,
            tops: detectedTops,
            assignments: detectedAssignments,
          });
          setSkippedAssignment(false);
          setIsProcessing(false);
          setCurrentStep(2);
        } else {
          setSkippedAssignment(true);
          setTops([DEFAULT_TOP_TITLE]);
          setAssignments(new Array(transcriptResult.length).fill(0));
          setIsProcessing(false);
          setCurrentStep(3);
          generateSummaryForAll(transcriptResult, job.job_id);
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
          setTops([DEFAULT_TOP_TITLE]);
          setAssignments(new Array(transcriptResult.length).fill(0));
          setCurrentStep(3);
          generateSummaryForAll(transcriptResult, job.job_id);
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
      if (restoreCandidate.sessionId) {
        const restored = await loadSession(restoreCandidate.sessionId);
        applySession(restored);
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

  const handleStartNewSession = () => {
    resetSession();
  };

  // Generate summary for entire conversation (when no TOPs are defined)
  const generateSummaryForAll = async (
    transcriptLines: TranscriptLine[],
    currentJobId: string
  ) => {
    setIsGeneratingSummary(true);
    setSummaries({ 0: "Zusammenfassung wird generiert..." });
    setSummaryReviews({});

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

      // Send telemetry
      if (telemetryOptIn && currentJobId) {
        reportSessionComplete({
          telemetryConsent: telemetryOptIn,
          jobId: currentJobId,
          topCount: 1,
          protocolCharCount: result.summary.length,
          summarizationDurationSeconds: result.durationSeconds,
          llmModel: llmSettings.model,
          systemPromptKind: getSystemPromptKind(
            GENERIC_SUMMARY_PROMPT,
            GENERIC_SUMMARY_PROMPT
          ),
        });
      }
    } catch (error) {
      const errorMessage =
        error instanceof Error ? error.message : "Unbekannter Fehler";
      setSummaries({ 0: `Fehler: ${errorMessage}` });
      setSummaryReviews({});
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
    startTranscription();
  };

  const handleStep2Next = async () => {
    // Move to step 3 first
    setCurrentStep(3);

    let totalDuration = 0;

    // Generate summaries for each TOP with assigned lines
    const validTops = tops.filter((t) => t.trim() !== "");
    const newSummaries: Record<number, string> = {};
    const newSummaryReviews: Record<number, SummaryReview> = {};

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

    // Send telemetry after all summaries are generated
    if (telemetryOptIn && jobId) {
      const protocolCharCount = Object.values(newSummaries).reduce(
        (sum, s) => sum + (s?.length || 0),
        0
      );
      reportSessionComplete({
        telemetryConsent: telemetryOptIn,
        jobId,
        topCount: validTops.length,
        protocolCharCount,
        summarizationDurationSeconds: totalDuration,
        llmModel: llmSettings.model,
        systemPromptKind: getSystemPromptKind(
          llmSettings.systemPrompt,
          GENERIC_SUMMARY_PROMPT
        ),
      });
      console.log(`[Summary] Telemetry sent`);
    }
  };

  const handleStep2Back = () => {
    setCurrentStep(1);
    setTranscript([]);
    setAssignments([]);
    setAgendaDetection(null);
    setAgendaDetectionError(null);
    setProcessingError(null);
    setAudioUrl(null);
  };

  const handleStep3Back = () => {
    if (skippedAssignment) {
      // Go back to upload step, reset auto-created TOP
      setCurrentStep(1);
      setTops(EMPTY_TOPS);
      setTranscript([]);
      setAssignments([]);
      setAgendaDetection(null);
      setAgendaDetectionError(null);
      setSummaries({});
      setSummaryReviews({});
      setAudioUrl(null);
      setSkippedAssignment(false);
      setIsGeneratingSummary(false);
    } else {
      setCurrentStep(2);
    }
  };

  const handleRegenerateSummary = async (topIndex: number) => {
    setIsGeneratingSummary(true);

    const validTops = tops.filter((t) => t.trim() !== "");
    const topLines = transcript.filter((_, i) => assignments[i] === topIndex);
    const prompt = skippedAssignment
      ? GENERIC_SUMMARY_PROMPT
      : llmSettings.systemPrompt;

    try {
      const result = await generateSummary(
        validTops[topIndex]!,
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

      {isProcessing ? (
        <div>
          <ProcessingStep
            progress={processingProgress}
            status={processingStatus}
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
          tops={tops}
          setTops={setTops}
          llmSettings={llmSettings}
          telemetryOptIn={telemetryOptIn}
          setTelemetryOptIn={setTelemetryOptIn}
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
        />
      ) : (
        <SummaryStep
          onBack={handleStep3Back}
          tops={validTops}
          transcript={transcript}
          assignments={assignments}
          summaries={summaries}
          setSummaries={setSummaries}
          summaryReviews={summaryReviews}
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

/**
 * Shared type definitions for the Protokollierungsassistenz
 */

// API Types
export interface TranscriptLine {
  speaker: string;
  text: string;
  start: number;  // Start time in seconds
  end: number;    // End time in seconds
}

export interface AudioMetadata {
  filename?: string | null;
  content_type?: string | null;
  size_bytes?: number | null;
}

export interface SpeakerProfile {
  profile_id: string;
  display_name: string;
  scope?: string | null;
  created_at: number;
  updated_at: number;
  archived_at?: number | null;
  archived: boolean;
}

export interface SpeakerObservation {
  observation_id: number;
  job_id: string;
  session_id: string;
  local_speaker_id: string;
  local_display_name: string;
  profile_id?: string | null;
  profile_display_name?: string | null;
  profile?: SpeakerProfile | null;
  confidence?: number | null;
  status: 'suggested' | 'confirmed' | 'rejected' | 'manual' | string;
  display_name: string;
  created_at: number;
  updated_at: number;
}

export interface SpeakerMatchDiagnostic {
  local_speaker_id: string;
  reason_code: string;
  reason: string;
  best_profile_id?: string | null;
  best_profile_display_name?: string | null;
  best_score?: number | null;
  suggest_threshold?: number | null;
  local_audio_seconds?: number | null;
  local_embedding_available: boolean;
  profile_embedding_count: number;
}

export interface SpeakerSuggestion {
  observation_id: number;
  local_speaker_id: string;
  profile_id: string;
  profile_display_name: string;
  confidence: number;
  status: string;
}

export interface TranscriptionJob {
  job_id: string;
  status: 'pending' | 'processing' | 'completed' | 'failed' | 'cancelled';
  progress: number;
  message: string;
  transcript?: TranscriptLine[];
  speaker_suggestions?: SpeakerSuggestion[];
  audio_url?: string;  // URL to stream audio for playback
  audio_metadata?: AudioMetadata | null;
  error?: string;
}

export type PipelineStatus = 'pending' | 'processing' | 'completed' | 'failed' | 'cancelled';

export type PipelineStage =
  | 'upload'
  | 'transcribe'
  | 'speaker_match'
  | 'agenda_detect'
  | 'summarize'
  | 'ready_for_review'
  | string;

export interface PipelineJob {
  pipeline_id: string;
  session_id?: string | null;
  transcription_job_id?: string | null;
  status: PipelineStatus;
  stage: PipelineStage;
  progress: number;
  warnings: string[];
  error?: string | null;
  created_at?: number | null;
  updated_at?: number | null;
}

export interface PipelineStartOptions {
  sessionId?: string | null;
  tops?: string[];
  pdfFile?: File | null;
  model?: string;
  systemPrompt?: string;
  rememberSpeakers?: boolean;
  skipAgendaDetection?: boolean;
}

export interface PipelineResultResponse {
  pipeline: PipelineJob;
  session: SessionResponse;
  job?: TranscriptionJob | null;
  speaker_observations?: SpeakerObservation[];
  summary_reviews?: Record<number, SummaryReview> | Record<string, SummaryReview>;
  warnings: string[];
  agenda_detection?: AgendaDetectionResponse | null;
}

export interface SessionSavePayload {
  session_id?: string | null;
  job_id?: string | null;
  current_step?: number | null;
  tops: string[];
  transcript?: TranscriptLine[];
  assignments: (number | null)[];
  speaker_names: Record<string, string>;
  summaries: Record<number, string>;
  summary_reviews?: Record<number, SummaryReview>;
  export_metadata?: ExportMetadata;
  skipped_assignment: boolean;
}

export interface SessionResponse extends SessionSavePayload {
  session_id: string;
  audio_url?: string | null;
  audio_metadata?: AudioMetadata | null;
  job?: TranscriptionJob | null;
}

export interface SummarizeRequest {
  top_title: string;
  lines: TranscriptLine[];
}

export interface StructuredSummary {
  discussion: string[];
  decisions: string[];
  votes: string[];
  action_items: string[];
  open_points: string[];
  uncertainties: string[];
}

export interface SummarySourceLink {
  section: keyof StructuredSummary | string;
  item_index: number;
  item_text: string;
  line_indices: number[];
  start?: number | null;
  end?: number | null;
  excerpt: string;
  confidence: number;
  missing_source: boolean;
}

export interface SummaryReviewWarning {
  kind: 'missing_source' | 'missing_decision_signal' | string;
  message: string;
  severity: 'info' | 'warning' | 'error' | string;
  keyword?: string | null;
  section?: keyof StructuredSummary | string | null;
  item_index?: number | null;
  line_indices: number[];
  start?: number | null;
  end?: number | null;
  excerpt: string;
}

export interface SummaryReview {
  structured?: StructuredSummary | null;
  source_links: SummarySourceLink[];
  review_warnings: SummaryReviewWarning[];
  fallback_used?: boolean;
  chunks_processed?: number;
}

export interface SummarizeResponse {
  summary: string;
  duration_seconds?: number;
  structured?: StructuredSummary | null;
  source_links?: SummarySourceLink[];
  review_warnings?: SummaryReviewWarning[];
  fallback_used?: boolean;
  chunks_processed?: number;
}

export interface AssignmentSuggestionSegment {
  top_index: number;
  top_title: string;
  start_index: number;
  end_index: number;
  confidence: number;
  uncertain: boolean;
  transition_type: 'explicit' | 'keyword' | 'inferred' | string;
  reason: string;
  evidence_index?: number | null;
  evidence_text?: string | null;
}

export interface AssignmentSuggestionsResponse {
  suggested_assignments: (number | null)[];
  segments: AssignmentSuggestionSegment[];
  strategy: string;
  uncertain_count: number;
}

export interface AgendaDetectionRequest {
  tops?: string[];
  transcript: TranscriptLine[];
  model?: string;
  systemPrompt?: string;
}

export interface AgendaDetectionResponse {
  tops: string[];
  assignments: (number | null)[];
  segments: AssignmentSuggestionSegment[];
  strategy: string;
  uncertain_count: number;
}

export interface ExportMetadata {
  committee: string;
  date: string;
  location: string;
  title: string;
  participants: string[];
  includeSpeakerList: boolean;
  includeTranscriptExcerpt: boolean;
  includeGenerationNote: boolean;
}

export type ExportFormat = 'txt' | 'docx' | 'pdf';

// Component Props Types
export interface LayoutProps {
  children: React.ReactNode;
  onSettingsClick?: () => void;
}

export interface StepIndicatorProps {
  currentStep: number;
}

export interface LLMSettings {
  model: string;
  systemPrompt: string;
}

export interface UploadStepProps {
  onNext: () => void;
  audioFile: File | null;
  setAudioFile: (file: File | null) => void;
  pdfFile: File | null;
  setPdfFile: (file: File | null) => void;
  tops: string[];
  setTops: (tops: string[]) => void;
  llmSettings?: LLMSettings;
  rememberSpeakers: boolean;
  setRememberSpeakers: (enabled: boolean) => void;
  skipAgendaDetection: boolean;
  setSkipAgendaDetection: (enabled: boolean) => void;
}

export interface ProcessingStepProps {
  progress: number;
  status: string;
  pipeline?: PipelineJob | null;
  canCancel?: boolean;
  onCancel?: () => void;
}

export interface AssignmentStepProps {
  onNext: () => void;
  onBack: () => void;
  tops: string[];
  setTops: (tops: string[]) => void;
  transcript: TranscriptLine[];
  setTranscript: (transcript: TranscriptLine[]) => void;
  assignments: (number | null)[];
  setAssignments: (assignments: (number | null)[]) => void;
  agendaDetection?: AgendaDetectionResponse | null;
  agendaDetectionError?: string | null;
  audioUrl?: string;  // URL to stream audio for playback
  speakerNames: Record<string, string>;
  setSpeakerNames: (names: Record<string, string>) => void;
  sessionId?: string | null;
  hasSummaries?: boolean;
  rememberSpeakers?: boolean;
}

export interface SummaryStepProps {
  onBack: () => void;
  tops: string[];
  transcript: TranscriptLine[];
  assignments: (number | null)[];
  summaries: Record<number, string>;
  setSummaries: (summaries: Record<number, string>) => void;
  summaryReviews?: Record<number, SummaryReview>;
  onRegenerateSummary: (topIndex: number) => Promise<void>;
  isGenerating: boolean;
  audioUrl?: string;  // URL to stream audio for playback
  speakerNames: Record<string, string>;
  exportMetadata: ExportMetadata;
  setExportMetadata: (metadata: ExportMetadata) => void;
}

// Color palette type for TOPs
export interface TopColor {
  bg: string;
  border: string;
  text: string;
  dot: string;
}

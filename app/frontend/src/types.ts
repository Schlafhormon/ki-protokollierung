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

export interface TranscriptionJob {
  job_id: string;
  status: 'pending' | 'processing' | 'completed' | 'failed' | 'cancelled';
  progress: number;
  message: string;
  transcript?: TranscriptLine[];
  audio_url?: string;  // URL to stream audio for playback
  audio_metadata?: AudioMetadata | null;
  error?: string;
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
  tops: string[];
  setTops: (tops: string[]) => void;
  llmSettings?: LLMSettings;
  telemetryOptIn: boolean;
  setTelemetryOptIn: (enabled: boolean) => void;
}

export interface ProcessingStepProps {
  progress: number;
  status: string;
}

export interface AssignmentStepProps {
  onNext: () => void;
  onBack: () => void;
  tops: string[];
  transcript: TranscriptLine[];
  setTranscript: (transcript: TranscriptLine[]) => void;
  assignments: (number | null)[];
  setAssignments: (assignments: (number | null)[]) => void;
  audioUrl?: string;  // URL to stream audio for playback
  speakerNames: Record<string, string>;
  setSpeakerNames: (names: Record<string, string>) => void;
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

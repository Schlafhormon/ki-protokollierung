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

export interface TranscriptionJob {
  job_id: string;
  status: 'pending' | 'processing' | 'completed' | 'failed' | 'cancelled';
  progress: number;
  message: string;
  transcript?: TranscriptLine[];
  audio_url?: string;  // URL to stream audio for playback
  error?: string;
}

export interface SummarizeRequest {
  top_title: string;
  lines: TranscriptLine[];
}

export interface SummarizeResponse {
  summary: string;
}

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
  onRegenerateSummary: (topIndex: number) => Promise<void>;
  isGenerating: boolean;
  audioUrl?: string;  // URL to stream audio for playback
  speakerNames: Record<string, string>;
}

// Color palette type for TOPs
export interface TopColor {
  bg: string;
  border: string;
  text: string;
  dot: string;
}

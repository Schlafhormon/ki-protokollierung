import { render, screen, waitFor } from '@testing-library/react';
import userEvent from '@testing-library/user-event';
import { beforeEach, describe, expect, it, vi } from 'vitest';
import App from './App';
import {
  checkBackendHealth,
  detectAgenda,
  extractTOPsFromPDF,
  generateSummary,
  getPipelineResult,
  getPipelineStatus,
  loadSession,
  pollPipeline,
  pollTranscription,
  saveSession,
  startPipeline,
  startTranscription,
} from './api';
import type { PipelineJob, PipelineResultResponse } from './types';

vi.mock('./api', () => ({
  checkBackendHealth: vi.fn(),
  loadSession: vi.fn(),
  saveSession: vi.fn(),
  startPipeline: vi.fn(),
  pollPipeline: vi.fn(),
  getPipelineStatus: vi.fn(),
  getPipelineResult: vi.fn(),
  cancelPipeline: vi.fn(),
  startTranscription: vi.fn(),
  pollTranscription: vi.fn(),
  detectAgenda: vi.fn(),
  generateSummary: vi.fn(),
  extractTOPsFromPDF: vi.fn(),
  exportProtocol: vi.fn(),
  listSpeakerProfiles: vi.fn(() => Promise.resolve([])),
  deleteSpeakerProfileEmbeddings: vi.fn(),
  archiveSpeakerProfile: vi.fn(),
}));

const startedPipeline: PipelineJob = {
  pipeline_id: 'pipeline-1',
  session_id: 'session-1',
  transcription_job_id: 'job-1',
  status: 'pending',
  stage: 'upload',
  progress: 5,
  warnings: [],
};

const completedPipeline: PipelineJob = {
  ...startedPipeline,
  status: 'completed',
  stage: 'ready_for_review',
  progress: 100,
};

function pipelineResult(
  overrides: Partial<PipelineResultResponse['session']> = {},
  pipeline: PipelineJob = completedPipeline,
  warnings: string[] = [],
  agendaDetection: PipelineResultResponse['agenda_detection'] = null
): PipelineResultResponse {
  return {
    pipeline,
    session: {
      session_id: 'session-1',
      job_id: 'job-1',
      current_step: 3,
      tops: ['Haushalt'],
      transcript: [
        { speaker: 'SPEAKER_00', text: 'Der Haushalt wird beraten.', start: 0, end: 2 },
      ],
      assignments: [0],
      speaker_names: { SPEAKER_00: 'SPEAKER_00' },
      summaries: { 0: 'Der Haushalt wurde serverseitig zusammengefasst.' },
      summary_reviews: {},
      skipped_assignment: false,
      audio_url: '/api/audio/job-1',
      ...overrides,
    },
    job: {
      job_id: 'job-1',
      status: 'completed',
      progress: 100,
      message: 'Fertig',
      transcript: [
        { speaker: 'SPEAKER_00', text: 'Der Haushalt wird beraten.', start: 0, end: 2 },
      ],
      audio_url: '/api/audio/job-1',
    },
    speaker_observations: [],
    summary_reviews: overrides.summary_reviews ?? {},
    warnings,
    agenda_detection: agendaDetection,
  };
}

async function uploadAndStart(user = userEvent.setup()) {
  const { container } = render(<App />);
  const input = container.querySelector<HTMLInputElement>('input[type="file"][accept="audio/*"]');
  await user.upload(input!, new File(['audio'], 'meeting.mp3', { type: 'audio/mpeg' }));
  await user.click(screen.getByRole('button', { name: /automatisch verarbeiten/i }));
  return { container };
}

describe('App pipeline flow', () => {
  beforeEach(() => {
    vi.clearAllMocks();
    localStorage.clear();
    vi.mocked(checkBackendHealth).mockResolvedValue(true);
    vi.mocked(saveSession).mockResolvedValue({
      session_id: 'session-1',
      tops: ['Haushalt'],
      transcript: [],
      assignments: [],
      speaker_names: {},
      summaries: {},
      skipped_assignment: false,
    });
    vi.mocked(startPipeline).mockResolvedValue(startedPipeline);
    vi.mocked(pollPipeline).mockImplementation(async (_pipelineId, onStatus) => {
      onStatus?.({
        ...startedPipeline,
        status: 'processing',
        stage: 'transcribe',
        progress: 25,
      });
      onStatus?.({
        ...startedPipeline,
        status: 'processing',
        stage: 'summarize',
        progress: 82,
      });
      return completedPipeline;
    });
    vi.mocked(getPipelineResult).mockResolvedValue(pipelineResult());
    vi.mocked(loadSession).mockResolvedValue(pipelineResult().session);
    vi.mocked(startTranscription).mockResolvedValue({
      job_id: 'legacy-job',
      status: 'pending',
      progress: 0,
      message: 'Gestartet',
    });
    vi.mocked(pollTranscription).mockResolvedValue({
      job_id: 'legacy-job',
      status: 'completed',
      progress: 100,
      message: 'Fertig',
      transcript: [
        { speaker: 'SPEAKER_00', text: 'Klassischer Fallback.', start: 0, end: 1 },
      ],
    });
    vi.mocked(detectAgenda).mockRejectedValue(new Error('Agenda nicht verfügbar'));
    vi.mocked(generateSummary).mockResolvedValue({
      summary: 'Fallback-Zusammenfassung.',
      durationSeconds: 1,
      structured: null,
      sourceLinks: [],
      reviewWarnings: [],
      fallbackUsed: false,
      chunksProcessed: 1,
    });
  });

  it('starts the end-to-end pipeline from the upload UI', async () => {
    await uploadAndStart();

    await waitFor(() => {
      expect(startPipeline).toHaveBeenCalledWith(
        expect.any(File),
        expect.objectContaining({
          sessionId: 'session-1',
          tops: [],
          pdfFile: null,
          model: expect.any(String),
        })
      );
    });
  });

  it('starts auto-PDF processing without requiring immediate TOP extraction', async () => {
    const user = userEvent.setup();
    const { container } = render(<App />);
    const audioInput = container.querySelector<HTMLInputElement>('input[type="file"][accept="audio/*"]');
    const pdfInput = container.querySelector<HTMLInputElement>('input[accept=".pdf,application/pdf"]');
    const pdf = new File(['pdf'], 'agenda.pdf', { type: 'application/pdf' });

    await user.click(screen.getByRole('checkbox', {
      name: /TOPs automatisch aus PDF erkennen und direkt verarbeiten/i,
    }));
    await user.upload(audioInput!, new File(['audio'], 'meeting.mp3', { type: 'audio/mpeg' }));
    await user.upload(pdfInput!, pdf);
    await user.click(screen.getByRole('button', { name: /automatisch verarbeiten/i }));

    await waitFor(() => {
      expect(extractTOPsFromPDF).not.toHaveBeenCalled();
      expect(startPipeline).toHaveBeenCalledWith(
        expect.any(File),
        expect.objectContaining({
          sessionId: 'session-1',
          tops: [],
          pdfFile: pdf,
          autoDetectTopsFromPdf: true,
          skipAgendaDetection: false,
        })
      );
    });
  });

  it('sends PDF-extracted TOPs to the pipeline instead of re-detecting them', async () => {
    const user = userEvent.setup();
    const { container } = render(<App />);
    const audioInput = container.querySelector<HTMLInputElement>('input[type="file"][accept="audio/*"]');
    const pdfInput = container.querySelector<HTMLInputElement>('input[accept=".pdf,application/pdf"]');
    const pdf = new File(['pdf'], 'agenda.pdf', { type: 'application/pdf' });
    vi.mocked(extractTOPsFromPDF).mockResolvedValue(['Eröffnung', 'Haushalt']);

    await user.upload(audioInput!, new File(['audio'], 'meeting.mp3', { type: 'audio/mpeg' }));
    await user.upload(pdfInput!, pdf);
    await screen.findByDisplayValue('Eröffnung');
    await screen.findByDisplayValue('Haushalt');
    await user.click(screen.getByRole('button', { name: /automatisch verarbeiten/i }));

    await waitFor(() => {
      expect(startPipeline).toHaveBeenCalledWith(
        expect.any(File),
        expect.objectContaining({
          tops: ['Eröffnung', 'Haushalt'],
          pdfFile: pdf,
          autoDetectTopsFromPdf: false,
        })
      );
    });
  });

  it('polls status and offers the direct protocol path for safe results', async () => {
    await uploadAndStart();

    await waitFor(() => {
      expect(pollPipeline).toHaveBeenCalledWith('pipeline-1', expect.any(Function));
    });
    expect(await screen.findByRole('button', { name: /direkt zum protokoll/i })).toBeInTheDocument();
  });

  it('loads the completed result into app state without regenerating summaries', async () => {
    const user = userEvent.setup();
    await uploadAndStart(user);

    await user.click(await screen.findByRole('button', { name: /direkt zum protokoll/i }));

    expect(await screen.findByText('Der Haushalt wurde serverseitig zusammengefasst.')).toBeInTheDocument();
    expect(generateSummary).not.toHaveBeenCalled();
  });

  it('sets agenda detection from the pipeline result', async () => {
    vi.mocked(getPipelineResult).mockResolvedValue(
      pipelineResult(
        {},
        completedPipeline,
        [],
        {
          tops: ['Haushalt'],
          assignments: [0],
          strategy: 'known_agenda_heuristic',
          uncertain_count: 0,
          segments: [
            {
              top_index: 0,
              top_title: 'Haushalt',
              start_index: 0,
              end_index: 0,
              confidence: 0.95,
              uncertain: false,
              transition_type: 'explicit',
              reason: 'Explizite TOP-Nennung',
              evidence_index: 0,
              evidence_text: 'Der Haushalt wird beraten.',
            },
          ],
        }
      )
    );

    await uploadAndStart();

    expect(await screen.findByText(/1 Segmente, 0 unsicher/i)).toBeInTheDocument();
    expect(screen.getByText(/Strategie: known_agenda_heuristic/i)).toBeInTheDocument();
  });

  it('hides direct protocol when agenda detection is uncertain', async () => {
    vi.mocked(getPipelineResult).mockResolvedValue(
      pipelineResult(
        {},
        completedPipeline,
        [],
        {
          tops: ['Haushalt'],
          assignments: [0],
          strategy: 'llm_repaired',
          uncertain_count: 1,
          segments: [
            {
              top_index: 0,
              top_title: 'Haushalt',
              start_index: 0,
              end_index: 0,
              confidence: 0.45,
              uncertain: true,
              transition_type: 'repaired',
              reason: 'Segment wurde repariert',
              evidence_index: 0,
              evidence_text: 'Der Haushalt wird beraten.',
            },
          ],
        }
      )
    );

    await uploadAndStart();

    expect(await screen.findByText(/1 Segmente, 1 unsicher/i)).toBeInTheDocument();
    expect(screen.queryByRole('button', { name: /direkt zum protokoll/i })).not.toBeInTheDocument();
  });

  it('keeps the speaker review step when no TOPs are available', async () => {
    vi.mocked(getPipelineResult).mockResolvedValue(
      pipelineResult({
        current_step: 2,
        tops: [],
        assignments: [null],
        skipped_assignment: true,
        summaries: { 0: 'Gesamtes Gespräch wurde zusammengefasst.' },
      })
    );

    await uploadAndStart();

    expect(await screen.findByText(/Sprecher umbenennen und Profile prüfen/i)).toBeInTheDocument();
    expect(screen.getByText(/Keine TOPs angelegt/i)).toBeInTheDocument();
    expect(screen.queryByRole('button', { name: /direkt zum protokoll/i })).not.toBeInTheDocument();
  });

  it('treats pipeline summaries as stale after assignment inputs change and regenerates', async () => {
    const user = userEvent.setup();
    vi.mocked(generateSummary).mockResolvedValue({
      summary: 'Neu generierte Zusammenfassung.',
      durationSeconds: 2,
      structured: null,
      sourceLinks: [],
      reviewWarnings: [],
      fallbackUsed: false,
      chunksProcessed: 1,
    });

    await uploadAndStart(user);

    expect(await screen.findByRole('button', { name: /direkt zum protokoll/i })).toBeInTheDocument();
    expect(screen.getByRole('button', { name: /^zum protokoll/i })).toBeInTheDocument();

    const topInput = screen.getByLabelText(/ausgewählter top/i);
    await user.clear(topInput);
    await user.type(topInput, 'Neuer Haushalt');
    await user.click(screen.getByRole('button', { name: /top umbenennen/i }));

    expect(screen.queryByRole('button', { name: /direkt zum protokoll/i })).not.toBeInTheDocument();
    await user.click(screen.getByRole('button', { name: /zusammenfassungen erstellen/i }));

    await waitFor(() => {
      expect(generateSummary).toHaveBeenCalledWith(
        'Neuer Haushalt',
        expect.arrayContaining([
          expect.objectContaining({ text: 'Der Haushalt wird beraten.' }),
        ]),
        expect.any(Object)
      );
    });
    expect(await screen.findByText('Neu generierte Zusammenfassung.')).toBeInTheDocument();
  });

  it('falls back to the legacy flow when the pipeline fails', async () => {
    vi.mocked(startPipeline).mockRejectedValue(new Error('Pipeline nicht erreichbar'));

    await uploadAndStart();

    expect(await screen.findByText(/Sprecher umbenennen und Profile prüfen/i)).toBeInTheDocument();
    expect(screen.getByText(/Keine TOPs angelegt/i)).toBeInTheDocument();
    expect(generateSummary).not.toHaveBeenCalled();
    expect(startTranscription).toHaveBeenCalledWith(expect.any(File), 'session-1', false);
  });

  it('keeps the review step when automatic generation has uncertain assignments', async () => {
    vi.mocked(getPipelineResult).mockResolvedValue(
      pipelineResult({
        tops: ['Haushalt', 'Schulbau'],
        transcript: [
          { speaker: 'SPEAKER_00', text: 'Haushalt.', start: 0, end: 1 },
          { speaker: 'SPEAKER_01', text: 'Vielleicht Schulbau.', start: 2, end: 3 },
        ],
        assignments: [0, null],
        summaries: {
          0: 'Haushalt wurde zusammengefasst.',
          1: 'Schulbau wurde zusammengefasst.',
        },
      })
    );

    await uploadAndStart();

    expect(await screen.findByText('Automatisch erkannte Segmente')).toBeInTheDocument();
    expect(screen.getByText('1 von 2 Zeilen zugeordnet')).toBeInTheDocument();
    expect(screen.queryByRole('button', { name: /direkt zum protokoll/i })).not.toBeInTheDocument();
  });

  it('resumes an active pipeline after reload and applies the finished result', async () => {
    const user = userEvent.setup();
    localStorage.setItem('active-pipeline-id', 'pipeline-1');
    vi.mocked(getPipelineStatus).mockResolvedValue({
      ...startedPipeline,
      status: 'processing',
      stage: 'agenda_detect',
      progress: 72,
    });

    render(<App />);
    await user.click(
      await screen.findByRole('button', { name: /letzte sitzung fortsetzen/i })
    );

    await waitFor(() => {
      expect(getPipelineStatus).toHaveBeenCalledWith('pipeline-1');
      expect(pollPipeline).toHaveBeenCalledWith('pipeline-1', expect.any(Function));
    });
    expect(await screen.findByRole('button', { name: /direkt zum protokoll/i })).toBeInTheDocument();
  });

  it('clears a failed restored pipeline id and loads the saved session', async () => {
    const user = userEvent.setup();
    localStorage.setItem('active-pipeline-id', 'pipeline-1');
    localStorage.setItem('active-session-id', 'session-1');
    vi.mocked(getPipelineStatus).mockResolvedValue({
      ...startedPipeline,
      status: 'failed',
      stage: 'summarize',
      progress: 0,
      error: 'Pipeline wurde durch Backend-Neustart unterbrochen',
    });

    render(<App />);
    await user.click(
      await screen.findByRole('button', { name: /letzte sitzung fortsetzen/i })
    );

    await waitFor(() => {
      expect(loadSession).toHaveBeenCalledWith('session-1');
    });
    expect(localStorage.getItem('active-pipeline-id')).toBeNull();
    expect(await screen.findByText('Der Haushalt wurde serverseitig zusammengefasst.')).toBeInTheDocument();
  });
});

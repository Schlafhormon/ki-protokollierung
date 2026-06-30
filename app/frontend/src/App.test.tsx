import { render, screen, waitFor } from '@testing-library/react';
import userEvent from '@testing-library/user-event';
import { beforeEach, describe, expect, it, vi } from 'vitest';
import App from './App';
import {
  checkBackendHealth,
  detectAgenda,
  generateSummary,
  loadSession,
  pollTranscription,
  saveSession,
  startTranscription,
} from './api';

vi.mock('./api', () => ({
  checkBackendHealth: vi.fn(),
  loadSession: vi.fn(),
  saveSession: vi.fn(),
  startTranscription: vi.fn(),
  pollTranscription: vi.fn(),
  detectAgenda: vi.fn(),
  generateSummary: vi.fn(),
  reportSessionComplete: vi.fn(),
  extractTOPsFromPDF: vi.fn(),
  exportProtocol: vi.fn(),
}));

describe('App session restore', () => {
  beforeEach(() => {
    vi.clearAllMocks();
    localStorage.clear();
    vi.mocked(checkBackendHealth).mockResolvedValue(true);
    vi.mocked(saveSession).mockResolvedValue({
      session_id: 'session-1',
      tops: ['Begruessung'],
      transcript: [],
      assignments: [],
      speaker_names: {},
      summaries: {},
      skipped_assignment: false,
    });
    vi.mocked(startTranscription).mockResolvedValue({
      job_id: 'job-1',
      status: 'pending',
      progress: 0,
      message: 'Gestartet',
    });
    vi.mocked(pollTranscription).mockResolvedValue({
      job_id: 'job-1',
      status: 'completed',
      progress: 100,
      message: 'Fertig',
      transcript: [
        { speaker: 'SPEAKER_00', text: 'Kommen wir zu TOP 1 Haushalt.', start: 0, end: 2 },
        { speaker: 'SPEAKER_01', text: 'Der Haushalt wird beraten.', start: 3, end: 5 },
      ],
    });
    vi.mocked(generateSummary).mockResolvedValue({
      summary: 'Gesamtes Gespräch zusammengefasst.',
      durationSeconds: 1,
      structured: null,
      sourceLinks: [],
      reviewWarnings: [],
      fallbackUsed: false,
      chunksProcessed: 1,
    });
  });

  it('loads the last backend session from the restore action', async () => {
    const user = userEvent.setup();
    localStorage.setItem('active-session-id', 'session-1');
    vi.mocked(loadSession).mockResolvedValue({
      session_id: 'session-1',
      job_id: 'job-1',
      current_step: 3,
      tops: ['Begruessung'],
      transcript: [
        {
          speaker: 'SPEAKER_00',
          text: 'Korrigierter Willkommenstext',
          start: 0,
          end: 2,
        },
      ],
      assignments: [0],
      speaker_names: { SPEAKER_00: 'Alice' },
      summaries: { 0: 'Die Sitzung wurde fortgesetzt.' },
      skipped_assignment: false,
      audio_url: '/api/audio/job-1',
    });

    render(<App />);

    await user.click(
      await screen.findByRole('button', { name: /letzte sitzung fortsetzen/i })
    );

    await waitFor(() => {
      expect(loadSession).toHaveBeenCalledWith('session-1');
    });
    expect(await screen.findByText('Die Sitzung wurde fortgesetzt.')).toBeInTheDocument();
    expect(screen.getByText(/Korrigierter Willkommenstext/)).toBeInTheDocument();
    expect(screen.getByText('Sitzung aktiv')).toBeInTheDocument();
  });

  it('starts without initial TOPs and shows detected agenda assignments for review', async () => {
    const user = userEvent.setup();
    vi.mocked(detectAgenda).mockResolvedValue({
      tops: ['Haushalt', 'Schulbau'],
      assignments: [0, 1],
      strategy: 'heuristic_transcript_fallback',
      uncertain_count: 1,
      segments: [
        {
          top_index: 0,
          top_title: 'Haushalt',
          start_index: 0,
          end_index: 0,
          confidence: 0.88,
          uncertain: false,
          transition_type: 'explicit',
          reason: 'Explizite TOP-Ankündigung im Transkript.',
          evidence_index: 0,
          evidence_text: 'Kommen wir zu TOP 1 Haushalt.',
        },
        {
          top_index: 1,
          top_title: 'Schulbau',
          start_index: 1,
          end_index: 1,
          confidence: 0.5,
          uncertain: true,
          transition_type: 'inferred',
          reason: 'Grenze wurde geschätzt.',
          evidence_index: 1,
          evidence_text: 'Der Haushalt wird beraten.',
        },
      ],
    });

    const { container } = render(<App />);
    const input = container.querySelector<HTMLInputElement>('input[type="file"][accept="audio/*"]');
    await user.upload(input!, new File(['audio'], 'meeting.mp3', { type: 'audio/mpeg' }));
    await user.click(screen.getByRole('button', { name: /transkription starten/i }));

    expect(await screen.findByText('Automatisch erkannte Segmente')).toBeInTheDocument();
    expect(screen.getByText(/TOP 1: Haushalt/i)).toBeInTheDocument();
    expect(screen.getAllByText(/unsicher/i).length).toBeGreaterThan(0);
    expect(detectAgenda).toHaveBeenCalledWith(
      expect.objectContaining({
        tops: [],
        transcript: expect.any(Array),
      })
    );
  });

  it('runs agenda detection with known TOPs and auto-applies assignments for review', async () => {
    const user = userEvent.setup();
    vi.mocked(detectAgenda).mockResolvedValue({
      tops: ['Begruessung', 'Haushalt'],
      assignments: [0, 1],
      strategy: 'known_agenda_heuristic',
      uncertain_count: 0,
      segments: [
        {
          top_index: 0,
          top_title: 'Begruessung',
          start_index: 0,
          end_index: 0,
          confidence: 0.72,
          uncertain: false,
          transition_type: 'inferred',
          reason: 'Beginn des Transkripts.',
          evidence_index: 0,
          evidence_text: 'Kommen wir zu TOP 1 Haushalt.',
        },
        {
          top_index: 1,
          top_title: 'Haushalt',
          start_index: 1,
          end_index: 1,
          confidence: 0.91,
          uncertain: false,
          transition_type: 'keyword',
          reason: 'TOP-Begriff erkannt.',
          evidence_index: 1,
          evidence_text: 'Der Haushalt wird beraten.',
        },
      ],
    });

    const { container } = render(<App />);
    await user.type(screen.getByPlaceholderText('TOP 1 eingeben...'), 'Begruessung');
    await user.type(screen.getByPlaceholderText('TOP 2 eingeben...'), 'Haushalt');
    const input = container.querySelector<HTMLInputElement>('input[type="file"][accept="audio/*"]');
    await user.upload(input!, new File(['audio'], 'meeting.mp3', { type: 'audio/mpeg' }));
    await user.click(screen.getByRole('button', { name: /transkription starten/i }));

    expect(await screen.findByText('Automatisch erkannte Segmente')).toBeInTheDocument();
    expect(screen.getByText('2 von 2 Zeilen zugeordnet')).toBeInTheDocument();
    expect(detectAgenda).toHaveBeenCalledWith(
      expect.objectContaining({
        tops: ['Begruessung', 'Haushalt'],
        transcript: expect.any(Array),
      })
    );
  });

  it('falls back to the full conversation flow when agenda detection fails without TOPs', async () => {
    const user = userEvent.setup();
    vi.mocked(detectAgenda).mockRejectedValue(new Error('Dienst nicht verfügbar'));

    const { container } = render(<App />);
    const input = container.querySelector<HTMLInputElement>('input[type="file"][accept="audio/*"]');
    await user.upload(input!, new File(['audio'], 'meeting.mp3', { type: 'audio/mpeg' }));
    await user.click(screen.getByRole('button', { name: /transkription starten/i }));

    expect(await screen.findByText('Gesamtes Gespräch zusammengefasst.')).toBeInTheDocument();
    expect(generateSummary).toHaveBeenCalledWith(
      'Gesamtes Gespräch',
      expect.any(Array),
      expect.any(Object)
    );
  });
});

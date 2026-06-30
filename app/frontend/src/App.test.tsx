import { render, screen, waitFor } from '@testing-library/react';
import userEvent from '@testing-library/user-event';
import { beforeEach, describe, expect, it, vi } from 'vitest';
import App from './App';
import { checkBackendHealth, loadSession, saveSession } from './api';

vi.mock('./api', () => ({
  checkBackendHealth: vi.fn(),
  loadSession: vi.fn(),
  saveSession: vi.fn(),
  startTranscription: vi.fn(),
  pollTranscription: vi.fn(),
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
});

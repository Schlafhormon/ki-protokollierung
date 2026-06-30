import { afterEach, beforeEach, describe, expect, it, vi } from 'vitest';
import { loadSession, saveSession, startTranscription } from './api';

describe('api session client', () => {
  beforeEach(() => {
    vi.restoreAllMocks();
  });

  afterEach(() => {
    vi.unstubAllGlobals();
  });

  it('passes the session id when starting transcription', async () => {
    const fetchMock = vi.fn().mockResolvedValue({
      ok: true,
      json: () =>
        Promise.resolve({
          job_id: 'job-1',
          status: 'pending',
          progress: 0,
          message: 'Transkription gestartet',
        }),
    });
    vi.stubGlobal('fetch', fetchMock);

    await startTranscription(
      new File(['audio'], 'meeting.mp3', { type: 'audio/mpeg' }),
      'session-1'
    );

    const body = fetchMock.mock.calls[0]![1]!.body as FormData;
    expect(fetchMock).toHaveBeenCalledWith('/api/transcribe', {
      method: 'POST',
      body,
    });
    expect(body.get('session_id')).toBe('session-1');
  });

  it('saves and loads persisted session state', async () => {
    const fetchMock = vi
      .fn()
      .mockResolvedValueOnce({
        ok: true,
        json: () =>
          Promise.resolve({
            session_id: 'session-1',
            job_id: 'job-1',
            current_step: 3,
            tops: ['TOP 1'],
            transcript: [
              {
                speaker: 'SPEAKER_00',
                text: 'Korrigierter Text',
                start: 0,
                end: 1,
              },
            ],
            assignments: [0],
            speaker_names: { SPEAKER_00: 'Alice' },
            summaries: { 0: 'Zusammenfassung' },
            skipped_assignment: false,
          }),
      })
      .mockResolvedValueOnce({
        ok: true,
        json: () =>
          Promise.resolve({
            session_id: 'session-1',
            tops: ['TOP 1'],
            assignments: [0],
            speaker_names: {},
            summaries: {},
            skipped_assignment: false,
          }),
      });
    vi.stubGlobal('fetch', fetchMock);

    await saveSession({
      session_id: 'session-1',
      job_id: 'job-1',
      current_step: 3,
      tops: ['TOP 1'],
      transcript: [
        { speaker: 'SPEAKER_00', text: 'Korrigierter Text', start: 0, end: 1 },
      ],
      assignments: [0],
      speaker_names: { SPEAKER_00: 'Alice' },
      summaries: { 0: 'Zusammenfassung' },
      skipped_assignment: false,
    });
    await loadSession('session-1');

    expect(fetchMock.mock.calls[0]![0]).toBe('/api/sessions/session-1');
    expect(fetchMock.mock.calls[0]![1]!.method).toBe('PUT');
    expect(JSON.parse(fetchMock.mock.calls[0]![1]!.body as string)).toMatchObject({
      transcript: [{ text: 'Korrigierter Text' }],
      speaker_names: { SPEAKER_00: 'Alice' },
      summaries: { 0: 'Zusammenfassung' },
    });
    expect(fetchMock.mock.calls[1]![0]).toBe('/api/sessions/session-1');
    expect(fetchMock.mock.calls[1]![1]).toBeUndefined();
  });
});

import { afterEach, beforeEach, describe, expect, it, vi } from 'vitest';
import {
  archiveSpeakerProfile,
  confirmSpeakerObservation,
  createManualSpeakerObservation,
  createSpeakerProfile,
  exportProtocol,
  listSpeakerObservations,
  listSpeakerProfiles,
  loadSession,
  reportSessionComplete,
  saveSession,
  startTranscription,
  updateSpeakerProfile,
} from './api';

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

  it('posts protocol export data and returns the generated blob', async () => {
    const blob = new Blob(['docx-bytes'], {
      type: 'application/vnd.openxmlformats-officedocument.wordprocessingml.document',
    });
    const fetchMock = vi.fn().mockResolvedValue({
      ok: true,
      blob: () => Promise.resolve(blob),
    });
    vi.stubGlobal('fetch', fetchMock);

    const result = await exportProtocol({
      format: 'docx',
      metadata: {
        committee: 'Hauptausschuss',
        date: '2026-06-30',
        location: 'Rathaus',
        title: 'Sitzung Hauptausschuss',
        participants: ['Alice'],
        includeSpeakerList: true,
        includeTranscriptExcerpt: true,
        includeGenerationNote: true,
      },
      tops: ['Begruessung'],
      transcript: [{ speaker: 'SPEAKER_00', text: 'Hallo', start: 0, end: 1 }],
      assignments: [0],
      speakerNames: { SPEAKER_00: 'Alice' },
      summaries: { 0: 'Diskussion:\nHallo' },
      summaryReviews: {},
    });

    expect(result).toBe(blob);
    expect(fetchMock.mock.calls[0]![0]).toBe('/api/export');
    const body = JSON.parse(fetchMock.mock.calls[0]![1]!.body as string);
    expect(body).toMatchObject({
      format: 'docx',
      metadata: {
        committee: 'Hauptausschuss',
        participants: ['Alice'],
      },
      appendix: {
        include_speaker_list: true,
        include_transcript_excerpt: true,
        include_generation_note: true,
      },
      speaker_names: { SPEAKER_00: 'Alice' },
    });
  });

  it('does not send telemetry when the user has not opted in', async () => {
    const fetchMock = vi.fn();
    vi.stubGlobal('fetch', fetchMock);

    await reportSessionComplete({
      telemetryConsent: false,
      jobId: 'job-1',
      topCount: 2,
      protocolCharCount: 120,
      summarizationDurationSeconds: 3.5,
      llmModel: 'qwen3:8b',
      systemPromptKind: 'custom',
    });

    expect(fetchMock).not.toHaveBeenCalled();
  });

  it('posts telemetry opt-in without prompt or content fields', async () => {
    const fetchMock = vi.fn().mockResolvedValue({
      ok: true,
      text: () => Promise.resolve(''),
    });
    vi.stubGlobal('fetch', fetchMock);

    await reportSessionComplete({
      telemetryConsent: true,
      jobId: 'job-1',
      topCount: 2,
      protocolCharCount: 120,
      summarizationDurationSeconds: 3.5,
      llmModel: 'qwen3:8b',
      systemPromptKind: 'custom',
    });

    expect(fetchMock.mock.calls[0]![0]).toBe('/api/telemetry/session-complete');
    const body = JSON.parse(fetchMock.mock.calls[0]![1]!.body as string);
    expect(body).toMatchObject({
      telemetry_consent: true,
      job_id: 'job-1',
      top_count: 2,
      protocol_char_count: 120,
      summarization_duration_seconds: 3.5,
      llm_model: 'qwen3:8b',
      system_prompt_kind: 'custom',
    });
    expect(body).not.toHaveProperty('system_prompt');
    expect(JSON.stringify(body)).not.toContain('Transkriptinhalt');
  });

  it('uses speaker profile and observation endpoints', async () => {
    const fetchMock = vi
      .fn()
      .mockResolvedValueOnce({
        ok: true,
        json: () =>
          Promise.resolve([
            {
              profile_id: 'alice',
              display_name: 'Alice Global',
              scope: null,
              created_at: 1,
              updated_at: 1,
              archived: false,
            },
          ]),
      })
      .mockResolvedValueOnce({
        ok: true,
        json: () =>
          Promise.resolve({
            profile_id: 'alice',
            display_name: 'Alice Lokal',
            scope: null,
            created_at: 1,
            updated_at: 2,
            archived: false,
          }),
      })
      .mockResolvedValueOnce({
        ok: true,
        json: () =>
          Promise.resolve({
            profile_id: 'alice',
            display_name: 'Alice Lokal',
            scope: null,
            created_at: 1,
            updated_at: 3,
            archived: true,
            archived_at: 3,
          }),
      })
      .mockResolvedValueOnce({
        ok: true,
        json: () =>
          Promise.resolve({
            profile_id: 'bob',
            display_name: 'Bob Global',
            scope: 'committee-1',
            created_at: 1,
            updated_at: 1,
            archived: false,
          }),
      })
      .mockResolvedValueOnce({
        ok: true,
        json: () =>
          Promise.resolve([
            {
              observation_id: 7,
              job_id: 'job-1',
              session_id: 'session-1',
              local_speaker_id: 'SPEAKER_00',
              local_display_name: 'SPEAKER_00',
              profile_id: 'alice',
              profile_display_name: 'Alice Lokal',
              confidence: 0.82,
              status: 'suggested',
              display_name: 'Alice Lokal',
              created_at: 1,
              updated_at: 1,
            },
          ]),
      })
      .mockResolvedValue({
        ok: true,
        json: () =>
          Promise.resolve({
            observation_id: 7,
            job_id: 'job-1',
            session_id: 'session-1',
            local_speaker_id: 'SPEAKER_00',
            local_display_name: 'SPEAKER_00',
            profile_id: 'alice',
            profile_display_name: 'Alice Lokal',
            confidence: 1,
            status: 'manual',
            display_name: 'Alice Lokal',
            created_at: 1,
            updated_at: 4,
          }),
      });
    vi.stubGlobal('fetch', fetchMock);

    await listSpeakerProfiles({ includeArchived: true });
    await updateSpeakerProfile('alice', { displayName: 'Alice Lokal' });
    await archiveSpeakerProfile('alice');
    await createSpeakerProfile({ displayName: 'Bob Global', scope: 'committee-1' });
    await listSpeakerObservations('session-1');
    await confirmSpeakerObservation('session-1', 7, { profileId: 'alice' });
    await createManualSpeakerObservation('session-1', {
      localSpeakerId: 'SPEAKER_00',
      profileId: 'alice',
      observationId: 7,
    });

    expect(fetchMock.mock.calls[0]![0]).toBe(
      '/api/speaker-profiles?include_archived=true'
    );
    expect(fetchMock.mock.calls[1]![0]).toBe('/api/speaker-profiles/alice');
    expect(fetchMock.mock.calls[1]![1]!.method).toBe('PUT');
    expect(JSON.parse(fetchMock.mock.calls[1]![1]!.body as string)).toEqual({
      display_name: 'Alice Lokal',
    });
    expect(fetchMock.mock.calls[2]![0]).toBe('/api/speaker-profiles/alice');
    expect(fetchMock.mock.calls[2]![1]!.method).toBe('DELETE');
    expect(fetchMock.mock.calls[3]![0]).toBe('/api/speaker-profiles');
    expect(fetchMock.mock.calls[3]![1]!.method).toBe('POST');
    expect(JSON.parse(fetchMock.mock.calls[3]![1]!.body as string)).toMatchObject({
      display_name: 'Bob Global',
      scope: 'committee-1',
    });
    expect(fetchMock.mock.calls[4]![0]).toBe(
      '/api/sessions/session-1/speaker-observations'
    );
    expect(fetchMock.mock.calls[5]![0]).toBe(
      '/api/sessions/session-1/speaker-observations/7/confirm'
    );
    expect(JSON.parse(fetchMock.mock.calls[5]![1]!.body as string)).toMatchObject({
      profile_id: 'alice',
    });
    expect(fetchMock.mock.calls[6]![0]).toBe(
      '/api/sessions/session-1/speaker-observations/manual'
    );
    expect(JSON.parse(fetchMock.mock.calls[6]![1]!.body as string)).toMatchObject({
      local_speaker_id: 'SPEAKER_00',
      profile_id: 'alice',
      observation_id: 7,
    });
  });
});

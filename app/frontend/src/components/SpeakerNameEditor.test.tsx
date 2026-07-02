import { render, screen, waitFor } from '@testing-library/react';
import userEvent from '@testing-library/user-event';
import type { ComponentProps } from 'react';
import { afterEach, beforeEach, describe, expect, it, vi } from 'vitest';
import type { TranscriptLine } from '../types';
import SpeakerNameEditor from './SpeakerNameEditor';

const transcript: TranscriptLine[] = [
  { speaker: 'SPEAKER_00', text: 'Hallo zusammen', start: 0, end: 4 },
  { speaker: 'SPEAKER_01', text: 'Wir beraten den Haushalt', start: 5, end: 9 },
];

function jsonResponse(data: unknown) {
  return {
    ok: true,
    json: () => Promise.resolve(data),
  };
}

const aliceProfile = {
  profile_id: 'alice',
  display_name: 'Alice Global',
  scope: null,
  created_at: 1,
  updated_at: 1,
  archived: false,
  embedding_count: 0,
};

const aliceSuggestion = {
  observation_id: 7,
  job_id: 'job-1',
  session_id: 'session-1',
  local_speaker_id: 'SPEAKER_00',
  local_display_name: 'SPEAKER_00',
  profile_id: 'alice',
  profile_display_name: 'Alice Global',
  confidence: 0.82,
  status: 'suggested',
  display_name: 'Alice Global',
  created_at: 1,
  updated_at: 1,
};

function renderEditor(overrides: Partial<ComponentProps<typeof SpeakerNameEditor>> = {}) {
  return render(
    <SpeakerNameEditor
      transcript={transcript}
      setTranscript={vi.fn()}
      speakerNames={{}}
      setSpeakerNames={vi.fn()}
      sessionId="session-1"
      rememberSpeakers
      {...overrides}
    />
  );
}

describe('SpeakerNameEditor', () => {
  beforeEach(() => {
    vi.clearAllMocks();
  });

  afterEach(() => {
    vi.unstubAllGlobals();
  });

  it('confirms an automatic speaker suggestion after review', async () => {
    const user = userEvent.setup();
    const setSpeakerNames = vi.fn();
    const fetchMock = vi
      .fn()
      .mockResolvedValueOnce(jsonResponse([aliceProfile]))
      .mockResolvedValueOnce(jsonResponse([aliceSuggestion]))
      .mockResolvedValueOnce(jsonResponse([]))
      .mockResolvedValueOnce(
        jsonResponse({ ...aliceSuggestion, status: 'confirmed' })
      )
      .mockResolvedValueOnce(jsonResponse([aliceProfile]))
      .mockResolvedValueOnce(jsonResponse([]));
    vi.stubGlobal('fetch', fetchMock);

    renderEditor({ setSpeakerNames });

    await screen.findByText('Vorschlag: Alice Global');
    await user.click(screen.getByRole('button', { name: /vorschlag übernehmen/i }));

    await waitFor(() =>
      expect(setSpeakerNames).toHaveBeenCalledWith({
        SPEAKER_00: 'Alice Global',
      })
    );
      expect(fetchMock.mock.calls[3]![0]).toBe(
      '/api/sessions/session-1/speaker-observations/7/confirm'
    );
  });

  it('rejects a false automatic suggestion without creating a profile', async () => {
    const user = userEvent.setup();
    const fetchMock = vi
      .fn()
      .mockResolvedValueOnce(jsonResponse([aliceProfile]))
      .mockResolvedValueOnce(jsonResponse([aliceSuggestion]))
      .mockResolvedValueOnce(jsonResponse([]))
      .mockResolvedValueOnce(jsonResponse({ ...aliceSuggestion, status: 'rejected' }))
      .mockResolvedValueOnce(jsonResponse([]));
    vi.stubGlobal('fetch', fetchMock);

    renderEditor();

    await screen.findByText('Vorschlag: Alice Global');
    await user.click(screen.getByRole('button', { name: /^ablehnen$/i }));

    await waitFor(() =>
        expect(fetchMock.mock.calls[3]![0]).toBe(
        '/api/sessions/session-1/speaker-observations/7/reject'
      )
    );
    expect(fetchMock).toHaveBeenCalledTimes(5);
  });

  it('stores a new profile only through the explicit remember action', async () => {
    const user = userEvent.setup();
    const setSpeakerNames = vi.fn();
    const manualObservation = {
      ...aliceSuggestion,
      profile_id: 'charlie',
      profile_display_name: 'Charlie Global',
      confidence: 1,
      status: 'manual',
      display_name: 'Charlie Global',
    };
    const fetchMock = vi
      .fn()
      .mockResolvedValueOnce(jsonResponse([]))
      .mockResolvedValueOnce(jsonResponse([]))
      .mockResolvedValueOnce(jsonResponse([]))
      .mockResolvedValueOnce(jsonResponse(manualObservation))
      .mockResolvedValueOnce(
        jsonResponse([{ ...aliceProfile, profile_id: 'charlie', display_name: 'Charlie Global' }])
      )
      .mockResolvedValueOnce(jsonResponse([]));
    vi.stubGlobal('fetch', fetchMock);

    renderEditor({
      speakerNames: { SPEAKER_00: 'Charlie Global' },
      setSpeakerNames,
    });

    await screen.findAllByText(/kein automatischer profilvorschlag/i);
    await user.click(screen.getAllByRole('button', { name: /neues profil merken/i })[0]!);

    await waitFor(() =>
      expect(setSpeakerNames).toHaveBeenCalledWith({
        SPEAKER_00: 'Charlie Global',
      })
    );
    expect(fetchMock.mock.calls[3]![0]).toBe(
      '/api/sessions/session-1/speaker-observations/manual'
    );
    expect(JSON.parse(fetchMock.mock.calls[3]![1]!.body as string)).toMatchObject({
      local_speaker_id: 'SPEAKER_00',
      display_name: 'Charlie Global',
    });
  });

  it('assigns a local speaker to an existing profile', async () => {
    const user = userEvent.setup();
    const setSpeakerNames = vi.fn();
    const fetchMock = vi
      .fn()
      .mockResolvedValueOnce(jsonResponse([aliceProfile]))
      .mockResolvedValueOnce(jsonResponse([]))
      .mockResolvedValueOnce(jsonResponse([]))
      .mockResolvedValueOnce(jsonResponse({ ...aliceSuggestion, status: 'manual' }))
      .mockResolvedValueOnce(jsonResponse([aliceProfile]))
      .mockResolvedValueOnce(jsonResponse([]));
    vi.stubGlobal('fetch', fetchMock);

    renderEditor({ setSpeakerNames });

    await screen.findAllByText('Alice Global (0)');
    await user.selectOptions(
      screen.getByLabelText('SPEAKER_00 bestehendem Profil zuordnen'),
      'alice'
    );
    await user.click(
      screen.getAllByRole('button', { name: /bestehendem profil zuordnen/i })[0]!
    );

    await waitFor(() =>
        expect(fetchMock.mock.calls[3]![0]).toBe(
        '/api/sessions/session-1/speaker-observations/manual'
      )
    );
    expect(JSON.parse(fetchMock.mock.calls[3]![1]!.body as string)).toMatchObject({
      local_speaker_id: 'SPEAKER_00',
      profile_id: 'alice',
    });
    expect(setSpeakerNames).toHaveBeenCalledWith({
      SPEAKER_00: 'Alice Global',
    });
  });

  it('unassigns an accepted persistent speaker mapping before correction', async () => {
    const user = userEvent.setup();
    const acceptedObservation = {
      ...aliceSuggestion,
      status: 'manual',
      confidence: 1,
    };
    const fetchMock = vi
      .fn()
      .mockResolvedValueOnce(jsonResponse([aliceProfile]))
      .mockResolvedValueOnce(jsonResponse([acceptedObservation]))
      .mockResolvedValueOnce(jsonResponse([]))
      .mockResolvedValueOnce(
        jsonResponse({ ...acceptedObservation, status: 'rejected' })
      )
      .mockResolvedValueOnce(jsonResponse([aliceProfile]))
      .mockResolvedValueOnce(jsonResponse([]));
    vi.stubGlobal('fetch', fetchMock);

    renderEditor({ speakerNames: { SPEAKER_00: 'Alice Global' } });

    await screen.findByText('Dauerhaft zugeordnet: Alice Global');
    await user.click(screen.getByRole('button', { name: /zuordnung lösen/i }));

    await waitFor(() =>
      expect(fetchMock.mock.calls[3]![0]).toBe(
        '/api/sessions/session-1/speaker-observations/7/unassign'
      )
    );
    expect(await screen.findByText(/zuordnung wurde gelöst/i)).toBeInTheDocument();
    await waitFor(() =>
      expect(
        screen.queryByText('Dauerhaft zugeordnet: Alice Global')
      ).not.toBeInTheDocument()
    );
  });

  it('shows embedding warnings returned by persistent speaker actions', async () => {
    const user = userEvent.setup();
    const warning = 'Profil wurde zugeordnet, aber für diese Sitzung ist kein Sprecher-Embedding verfügbar.';
    const fetchMock = vi
      .fn()
      .mockResolvedValueOnce(jsonResponse([aliceProfile]))
      .mockResolvedValueOnce(jsonResponse([]))
      .mockResolvedValueOnce(jsonResponse([]))
      .mockResolvedValueOnce(
        jsonResponse({
          ...aliceSuggestion,
          status: 'manual',
          embedding_warning: warning,
        })
      )
      .mockResolvedValueOnce(jsonResponse([aliceProfile]))
      .mockResolvedValueOnce(jsonResponse([]));
    vi.stubGlobal('fetch', fetchMock);

    renderEditor();

    await screen.findAllByText('Alice Global (0)');
    await user.selectOptions(
      screen.getByLabelText('SPEAKER_00 bestehendem Profil zuordnen'),
      'alice'
    );
    await user.click(
      screen.getAllByRole('button', { name: /bestehendem profil zuordnen/i })[0]!
    );

    expect(await screen.findByText(warning)).toBeInTheDocument();
  });

  it('renames and archives saved profiles from the profile management area', async () => {
    const user = userEvent.setup();
    const renamedProfile = { ...aliceProfile, display_name: 'Alice Umbenannt' };
    const fetchMock = vi
      .fn()
      .mockResolvedValueOnce(jsonResponse([aliceProfile]))
      .mockResolvedValueOnce(jsonResponse([]))
      .mockResolvedValueOnce(jsonResponse([]))
      .mockResolvedValueOnce(jsonResponse(renamedProfile))
      .mockResolvedValueOnce(jsonResponse({ ...renamedProfile, archived: true }))
      .mockResolvedValueOnce(jsonResponse([]));
    vi.stubGlobal('fetch', fetchMock);

    renderEditor();

    const renameInput = await screen.findByLabelText('Profil umbenennen');
    await user.clear(renameInput);
    await user.type(renameInput, 'Alice Umbenannt');
    await user.click(screen.getByRole('button', { name: /^profil umbenennen$/i }));
    await user.click(screen.getByRole('button', { name: /^profil archivieren$/i }));

    await waitFor(() =>
      expect(fetchMock.mock.calls[4]![0]).toBe('/api/speaker-profiles/alice')
    );
    expect(fetchMock.mock.calls[3]![1]!.method).toBe('PUT');
    expect(JSON.parse(fetchMock.mock.calls[3]![1]!.body as string)).toEqual({
      display_name: 'Alice Umbenannt',
    });
    expect(fetchMock.mock.calls[4]![1]!.method).toBe('DELETE');
  });

  it('can request profile embedding backfill from profile management', async () => {
    const user = userEvent.setup();
    const fetchMock = vi
      .fn()
      .mockResolvedValueOnce(jsonResponse([aliceProfile]))
      .mockResolvedValueOnce(jsonResponse([]))
      .mockResolvedValueOnce(jsonResponse([]))
      .mockResolvedValueOnce(
        jsonResponse({
          scanned_observation_count: 2,
          processed_job_count: 1,
          saved_embedding_count: 3,
          skipped_count: 0,
          errors: [],
        })
      )
      .mockResolvedValueOnce(jsonResponse([{ ...aliceProfile, embedding_count: 3 }]))
      .mockResolvedValueOnce(jsonResponse([]));
    vi.stubGlobal('fetch', fetchMock);

    renderEditor();

    await screen.findByLabelText('Gespeichertes Profil auswählen');
    await user.click(screen.getByRole('button', { name: /embeddings nachholen/i }));

    await waitFor(() =>
      expect(fetchMock.mock.calls[3]![0]).toBe(
        '/api/speaker-embeddings/backfill?profile_id=alice'
      )
    );
    expect(await screen.findByText(/3 Embeddings nachgeholt/i)).toBeInTheDocument();
  });

  it('shows the diagnostic reason when no automatic suggestion was created', async () => {
    const fetchMock = vi
      .fn()
      .mockResolvedValueOnce(jsonResponse([aliceProfile]))
      .mockResolvedValueOnce(jsonResponse([]))
      .mockResolvedValueOnce(
        jsonResponse([
          {
            local_speaker_id: 'SPEAKER_00',
            reason_code: 'below_threshold',
            reason: 'unter Schwellwert',
            best_profile_id: 'alice',
            best_profile_display_name: 'Alice Global',
            best_score: 0.61,
            suggest_threshold: 0.72,
            local_audio_seconds: 14,
            local_embedding_available: true,
            profile_embedding_count: 4,
          },
        ])
      );
    vi.stubGlobal('fetch', fetchMock);

    renderEditor();

    expect(await screen.findByText(/Grund: unter Schwellwert/i)).toBeInTheDocument();
  });

  it('does not load or show persistent profiles while speaker memory is off', async () => {
    const fetchMock = vi.fn();
    vi.stubGlobal('fetch', fetchMock);

    renderEditor({ rememberSpeakers: false });

    expect(await screen.findByText(/Dauerhafte Sprecherprofile sind ausgeschaltet/i)).toBeInTheDocument();
    expect(screen.getByLabelText('SPEAKER_00 lokal benennen')).toBeInTheDocument();
    expect(screen.queryByRole('button', { name: /neues profil merken/i })).not.toBeInTheDocument();
    expect(fetchMock).not.toHaveBeenCalled();
  });
});

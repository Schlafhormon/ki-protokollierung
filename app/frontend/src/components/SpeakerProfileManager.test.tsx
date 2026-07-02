import { render, screen, waitFor } from '@testing-library/react';
import userEvent from '@testing-library/user-event';
import { afterEach, beforeEach, describe, expect, it, vi } from 'vitest';
import SpeakerProfileManager from './SpeakerProfileManager';

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
  embedding_count: 3,
};

const rudolfProfile = {
  profile_id: 'rudolf',
  display_name: 'Herr Rudolf',
  scope: null,
  created_at: 1,
  updated_at: 1,
  archived: false,
  embedding_count: 0,
};

describe('SpeakerProfileManager', () => {
  beforeEach(() => {
    vi.clearAllMocks();
  });

  afterEach(() => {
    vi.unstubAllGlobals();
  });

  it('lists current speaker profiles in the settings area', async () => {
    vi.stubGlobal(
      'fetch',
      vi.fn().mockResolvedValueOnce(jsonResponse([aliceProfile, rudolfProfile]))
    );

    render(<SpeakerProfileManager />);

    expect(await screen.findByText('Alice Global')).toBeInTheDocument();
    expect(screen.getByText('Herr Rudolf')).toBeInTheDocument();
    expect(screen.getByText('3 Embeddings')).toBeInTheDocument();
    expect(screen.getByText('0 Embeddings')).toBeInTheDocument();
  });

  it('deletes embeddings for one profile and refreshes the list', async () => {
    const user = userEvent.setup();
    const fetchMock = vi
      .fn()
      .mockResolvedValueOnce(jsonResponse([aliceProfile]))
      .mockResolvedValueOnce(jsonResponse({ profile_id: 'alice', deleted_count: 3 }))
      .mockResolvedValueOnce(
        jsonResponse([{ ...aliceProfile, embedding_count: 0 }])
      );
    vi.stubGlobal('fetch', fetchMock);

    render(<SpeakerProfileManager />);

    await screen.findByText('Alice Global');
    await user.click(
      screen.getByRole('button', {
        name: 'Embeddings von Alice Global löschen',
      })
    );

    await waitFor(() =>
      expect(fetchMock.mock.calls[1]![0]).toBe(
        '/api/speaker-profiles/alice/embeddings'
      )
    );
    expect(fetchMock.mock.calls[1]![1]!.method).toBe('DELETE');
    expect(
      await screen.findByText('3 Embeddings von Alice Global wurden gelöscht.')
    ).toBeInTheDocument();
    expect(await screen.findByText('0 Embeddings')).toBeInTheDocument();
  });

  it('removes a profile from the active list', async () => {
    const user = userEvent.setup();
    const fetchMock = vi
      .fn()
      .mockResolvedValueOnce(jsonResponse([rudolfProfile]))
      .mockResolvedValueOnce(
        jsonResponse({ ...rudolfProfile, archived: true, archived_at: 2 })
      )
      .mockResolvedValueOnce(jsonResponse([]));
    vi.stubGlobal('fetch', fetchMock);

    render(<SpeakerProfileManager />);

    await screen.findByText('Herr Rudolf');
    await user.click(
      screen.getByRole('button', {
        name: 'Profil Herr Rudolf entfernen',
      })
    );

    await waitFor(() =>
      expect(fetchMock.mock.calls[1]![0]).toBe('/api/speaker-profiles/rudolf')
    );
    expect(fetchMock.mock.calls[1]![1]!.method).toBe('DELETE');
    expect(await screen.findByText('Profil Herr Rudolf wurde entfernt.')).toBeInTheDocument();
    expect(
      await screen.findByText('Noch keine gespeicherten Sprecherprofile vorhanden.')
    ).toBeInTheDocument();
  });
});

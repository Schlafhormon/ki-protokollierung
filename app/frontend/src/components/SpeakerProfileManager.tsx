import { useEffect, useState } from 'react';
import {
  archiveSpeakerProfile,
  deleteSpeakerProfileEmbeddings,
  listSpeakerProfiles,
} from '../api';
import type { SpeakerProfile } from '../types';

interface SpeakerProfileManagerProps {
  className?: string;
}

export default function SpeakerProfileManager({
  className = '',
}: SpeakerProfileManagerProps) {
  const [profiles, setProfiles] = useState<SpeakerProfile[]>([]);
  const [isLoading, setIsLoading] = useState(true);
  const [actionProfileId, setActionProfileId] = useState<string | null>(null);
  const [message, setMessage] = useState<string | null>(null);
  const [error, setError] = useState<string | null>(null);

  const loadProfiles = async () => {
    const loadedProfiles = await listSpeakerProfiles();
    setProfiles(loadedProfiles);
  };

  useEffect(() => {
    let isMounted = true;
    setIsLoading(true);
    listSpeakerProfiles()
      .then((loadedProfiles) => {
        if (isMounted) {
          setProfiles(loadedProfiles);
          setError(null);
        }
      })
      .catch((loadError) => {
        if (isMounted) {
          setError(
            loadError instanceof Error
              ? loadError.message
              : 'Sprecherprofile konnten nicht geladen werden'
          );
        }
      })
      .finally(() => {
        if (isMounted) {
          setIsLoading(false);
        }
      });

    return () => {
      isMounted = false;
    };
  }, []);

  const handleDeleteEmbeddings = async (profile: SpeakerProfile) => {
    setActionProfileId(profile.profile_id);
    setMessage(null);
    setError(null);
    try {
      const result = await deleteSpeakerProfileEmbeddings(profile.profile_id);
      await loadProfiles();
      setMessage(
        result.deleted_count === 1
          ? `Ein Embedding von ${profile.display_name} wurde gelöscht.`
          : `${result.deleted_count} Embeddings von ${profile.display_name} wurden gelöscht.`
      );
    } catch (deleteError) {
      setError(
        deleteError instanceof Error
          ? deleteError.message
          : 'Embeddings konnten nicht gelöscht werden'
      );
    } finally {
      setActionProfileId(null);
    }
  };

  const handleRemoveProfile = async (profile: SpeakerProfile) => {
    setActionProfileId(profile.profile_id);
    setMessage(null);
    setError(null);
    try {
      await archiveSpeakerProfile(profile.profile_id);
      await loadProfiles();
      setMessage(`Profil ${profile.display_name} wurde entfernt.`);
    } catch (archiveError) {
      setError(
        archiveError instanceof Error
          ? archiveError.message
          : 'Profil konnte nicht entfernt werden'
      );
    } finally {
      setActionProfileId(null);
    }
  };

  return (
    <section className={`border-t border-gray-200 pt-6 ${className}`}>
      <div className="mb-3">
        <h3 className="text-sm font-semibold text-gray-900">Profilverwaltung</h3>
        <p className="mt-1 text-xs text-gray-500">
          Dauerhaft gespeicherte Sprecherprofile und ihre Referenz-Embeddings.
        </p>
      </div>

      {message && (
        <div className="mb-3 rounded-md border border-green-200 bg-green-50 px-3 py-2 text-xs text-green-700">
          {message}
        </div>
      )}
      {error && (
        <div className="mb-3 rounded-md border border-red-200 bg-red-50 px-3 py-2 text-xs text-red-700">
          {error}
        </div>
      )}

      {isLoading ? (
        <p className="text-sm text-gray-500">Profile werden geladen...</p>
      ) : profiles.length === 0 ? (
        <p className="rounded-md border border-gray-200 bg-gray-50 px-3 py-3 text-sm text-gray-500">
          Noch keine gespeicherten Sprecherprofile vorhanden.
        </p>
      ) : (
        <div className="space-y-2">
          {profiles.map((profile) => {
            const isBusy = actionProfileId === profile.profile_id;
            return (
              <div
                key={profile.profile_id}
                className="rounded-md border border-gray-200 bg-white p-3"
              >
                <div className="flex flex-col gap-3 sm:flex-row sm:items-center sm:justify-between">
                  <div className="min-w-0">
                    <div className="truncate text-sm font-medium text-gray-900">
                      {profile.display_name}
                    </div>
                    <div className="mt-1 text-xs text-gray-500">
                      {profile.embedding_count ?? 0} Embeddings
                    </div>
                  </div>
                  <div className="flex flex-wrap gap-2">
                    <button
                      type="button"
                      onClick={() => handleDeleteEmbeddings(profile)}
                      disabled={isBusy || (profile.embedding_count ?? 0) === 0}
                      aria-label={`Embeddings von ${profile.display_name} löschen`}
                      className="rounded border border-red-200 bg-red-50 px-3 py-1.5 text-xs font-medium text-red-700 hover:bg-red-100 disabled:bg-gray-100 disabled:text-gray-400 disabled:border-gray-200"
                    >
                      Embeddings löschen
                    </button>
                    <button
                      type="button"
                      onClick={() => handleRemoveProfile(profile)}
                      disabled={isBusy}
                      aria-label={`Profil ${profile.display_name} entfernen`}
                      className="rounded border border-red-200 bg-white px-3 py-1.5 text-xs font-medium text-red-700 hover:bg-red-50 disabled:opacity-50"
                    >
                      Profil entfernen
                    </button>
                  </div>
                </div>
              </div>
            );
          })}
        </div>
      )}
    </section>
  );
}

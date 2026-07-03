import { useEffect, useMemo, useState, type Dispatch, type SetStateAction } from 'react';
import {
  archiveSpeakerProfile,
  backfillSpeakerEmbeddings,
  confirmSpeakerObservation,
  createManualSpeakerObservation,
  deleteSpeakerProfileEmbeddings,
  listSpeakerMatchDiagnostics,
  listSpeakerObservations,
  listSpeakerProfiles,
  rejectSpeakerObservation,
  unassignSpeakerObservation,
  updateSpeakerProfile,
} from '../api';
import type {
  SpeakerMatchDiagnostic,
  SpeakerObservation,
  SpeakerProfile,
  TranscriptLine,
} from '../types';
import AudioPlayer from './AudioPlayer';

interface SpeakerNameEditorProps {
  transcript: TranscriptLine[];
  setTranscript: (transcript: TranscriptLine[]) => void;
  speakerNames: Record<string, string>;
  setSpeakerNames: Dispatch<SetStateAction<Record<string, string>>>;
  sessionId?: string | null;
  rememberSpeakers?: boolean;
  audioUrl?: string;
}

type ReviewStatus = 'idle' | 'loading' | 'ready' | 'error';

function formatConfidence(confidence?: number | null): string {
  if (confidence === null || confidence === undefined) {
    return 'ohne Confidence';
  }
  return `${Math.round(confidence * 100)}% Confidence`;
}

function upsertObservation(
  observations: SpeakerObservation[],
  updated: SpeakerObservation
): SpeakerObservation[] {
  const index = observations.findIndex(
    (observation) => observation.observation_id === updated.observation_id
  );
  if (index === -1) {
    return [...observations, updated];
  }
  return observations.map((observation) =>
    observation.observation_id === updated.observation_id ? updated : observation
  );
}

function shouldApplyAcceptedProfileName(
  currentName: string | undefined,
  localSpeakerId: string
): boolean {
  const trimmed = currentName?.trim() ?? '';
  return !trimmed || trimmed.toLowerCase() === localSpeakerId.toLowerCase();
}

export default function SpeakerNameEditor({
  transcript,
  setTranscript,
  speakerNames,
  setSpeakerNames,
  sessionId,
  rememberSpeakers = false,
  audioUrl,
}: SpeakerNameEditorProps) {
  const speakerInfo = useMemo(() => {
    const speakers = new Map<string, { sample: string; start: number; lineIndex: number }>();
    for (const [lineIndex, line] of transcript.entries()) {
      if (!speakers.has(line.speaker)) {
        const sample =
          line.text.length > 60 ? `${line.text.substring(0, 60)}...` : line.text;
        speakers.set(line.speaker, { sample, start: line.start, lineIndex });
      }
    }
    return Array.from(speakers.entries()).map(([id, value]) => ({ id, ...value }));
  }, [transcript]);

  const [isExpanded, setIsExpanded] = useState(speakerInfo.length <= 3);
  const [mergeTargets, setMergeTargets] = useState<Record<string, string>>({});
  const [profiles, setProfiles] = useState<SpeakerProfile[]>([]);
  const [observations, setObservations] = useState<SpeakerObservation[]>([]);
  const [diagnostics, setDiagnostics] = useState<SpeakerMatchDiagnostic[]>([]);
  const [reviewStatus, setReviewStatus] = useState<ReviewStatus>('idle');
  const [reviewError, setReviewError] = useState<string | null>(null);
  const [actionMessage, setActionMessage] = useState<string | null>(null);
  const [actionSpeaker, setActionSpeaker] = useState<string | null>(null);
  const [profileTargets, setProfileTargets] = useState<Record<string, string>>({});
  const [selectedProfileId, setSelectedProfileId] = useState('');
  const [profileNameDraft, setProfileNameDraft] = useState('');
  const [sampleSeekTime, setSampleSeekTime] = useState<number | undefined>(undefined);

  useEffect(() => {
    if (!isExpanded || !sessionId) {
      return;
    }
    if (!rememberSpeakers) {
      setProfiles([]);
      setObservations([]);
      setDiagnostics([]);
      setReviewStatus('ready');
      setReviewError(null);
      return;
    }

    let isCurrent = true;
    setReviewStatus('loading');
    setReviewError(null);

    Promise.all([
      listSpeakerProfiles(),
      listSpeakerObservations(sessionId),
      listSpeakerMatchDiagnostics(sessionId),
    ])
      .then(([loadedProfiles, loadedObservations, loadedDiagnostics]) => {
        if (!isCurrent) {
          return;
        }
        setProfiles(loadedProfiles);
        setObservations(loadedObservations);
        setDiagnostics(loadedDiagnostics);
        setReviewStatus('ready');
        const firstProfile = loadedProfiles[0];
        if (firstProfile) {
          setSelectedProfileId((current) => current || firstProfile.profile_id);
          setProfileNameDraft((current) => current || firstProfile.display_name);
        }
      })
      .catch((error) => {
        if (!isCurrent) {
          return;
        }
        setReviewStatus('error');
        setReviewError(
          error instanceof Error
            ? error.message
            : 'Sprecherprofile konnten nicht geladen werden'
        );
      });

    return () => {
      isCurrent = false;
    };
  }, [isExpanded, sessionId, rememberSpeakers]);

  const suggestionsBySpeaker = useMemo(() => {
    const grouped = new Map<string, SpeakerObservation>();
    for (const observation of observations) {
      if (observation.status !== 'suggested' || !observation.profile_id) {
        continue;
      }
      const current = grouped.get(observation.local_speaker_id);
      if (
        !current ||
        (observation.confidence ?? 0) > (current.confidence ?? 0)
      ) {
        grouped.set(observation.local_speaker_id, observation);
      }
    }
    return grouped;
  }, [observations]);

  const acceptedBySpeaker = useMemo(() => {
    const grouped = new Map<string, SpeakerObservation>();
    for (const observation of observations) {
      if (observation.status === 'confirmed' || observation.status === 'manual') {
        grouped.set(observation.local_speaker_id, observation);
      }
    }
    return grouped;
  }, [observations]);

  useEffect(() => {
    if (!rememberSpeakers || observations.length === 0) {
      return;
    }

    setSpeakerNames((current) => {
      let changed = false;
      const next = { ...current };
      for (const observation of observations) {
        if (observation.status !== 'confirmed' && observation.status !== 'manual') {
          continue;
        }
        const displayName =
          observation.profile_display_name?.trim() || observation.display_name?.trim();
        if (!displayName) {
          continue;
        }
        if (
          shouldApplyAcceptedProfileName(
            next[observation.local_speaker_id],
            observation.local_speaker_id
          )
        ) {
          next[observation.local_speaker_id] = displayName;
          changed = true;
        }
      }
      return changed ? next : current;
    });
  }, [observations, rememberSpeakers, setSpeakerNames]);

  const diagnosticsBySpeaker = useMemo(() => {
    const grouped = new Map<string, SpeakerMatchDiagnostic>();
    for (const diagnostic of diagnostics) {
      grouped.set(diagnostic.local_speaker_id, diagnostic);
    }
    return grouped;
  }, [diagnostics]);

  const selectedProfile = profiles.find(
    (profile) => profile.profile_id === selectedProfileId
  );

  const refreshProfiles = async () => {
    const loadedProfiles = await listSpeakerProfiles();
    setProfiles(loadedProfiles);
    if (selectedProfileId && !loadedProfiles.some((profile) => profile.profile_id === selectedProfileId)) {
      const nextProfile = loadedProfiles[0];
      setSelectedProfileId(nextProfile?.profile_id ?? '');
      setProfileNameDraft(nextProfile?.display_name ?? '');
    }
  };

  const refreshDiagnostics = async () => {
    if (!sessionId || !rememberSpeakers) {
      return;
    }
    setDiagnostics(await listSpeakerMatchDiagnostics(sessionId));
  };

  const handleNameChange = (speakerId: string, name: string) => {
    setSpeakerNames((current) => ({
      ...current,
      [speakerId]: name,
    }));
  };

  const applyObservationName = (speakerId: string, observation: SpeakerObservation) => {
    setSpeakerNames((current) => ({
      ...current,
      [speakerId]: observation.display_name,
    }));
  };

  const runSpeakerAction = async (
    speakerId: string,
    action: () => Promise<SpeakerObservation>,
    successMessage: string
  ) => {
    setActionSpeaker(speakerId);
    setActionMessage(null);
    setReviewError(null);
    try {
      const updated = await action();
      setObservations((current) => upsertObservation(current, updated));
      applyObservationName(speakerId, updated);
      setActionMessage(updated.embedding_warning || successMessage);
      await refreshProfiles();
      await refreshDiagnostics();
    } catch (error) {
      setReviewError(
        error instanceof Error ? error.message : 'Sprecheraktion fehlgeschlagen'
      );
    } finally {
      setActionSpeaker(null);
    }
  };

  const handleConfirmSuggestion = (speakerId: string, suggestion: SpeakerObservation) => {
    if (!sessionId || !rememberSpeakers) {
      return;
    }
    runSpeakerAction(
      speakerId,
      () => confirmSpeakerObservation(sessionId, suggestion.observation_id),
      'Vorschlag wurde dauerhaft bestätigt.'
    );
  };

  const handleRejectSuggestion = async (suggestion: SpeakerObservation) => {
    if (!sessionId) {
      return;
    }
    setActionSpeaker(suggestion.local_speaker_id);
    setActionMessage(null);
    setReviewError(null);
    try {
      const updated = await rejectSpeakerObservation(
        sessionId,
        suggestion.observation_id
      );
      setObservations((current) => upsertObservation(current, updated));
      setActionMessage('Vorschlag wurde abgelehnt.');
      await refreshDiagnostics();
    } catch (error) {
      setReviewError(
        error instanceof Error ? error.message : 'Vorschlag konnte nicht abgelehnt werden'
      );
    } finally {
      setActionSpeaker(null);
    }
  };

  const handleUnassignAccepted = async (accepted: SpeakerObservation) => {
    if (!sessionId) {
      return;
    }
    setActionSpeaker(accepted.local_speaker_id);
    setActionMessage(null);
    setReviewError(null);
    try {
      const updated = await unassignSpeakerObservation(
        sessionId,
        accepted.observation_id
      );
      setObservations((current) => upsertObservation(current, updated));
      setActionMessage(
        'Zuordnung wurde gelöst. Der Sprecher kann jetzt neu zugeordnet werden.'
      );
      await refreshProfiles();
      await refreshDiagnostics();
    } catch (error) {
      setReviewError(
        error instanceof Error ? error.message : 'Zuordnung konnte nicht gelöst werden'
      );
    } finally {
      setActionSpeaker(null);
    }
  };

  const handleRememberNewProfile = (speakerId: string, suggestion?: SpeakerObservation) => {
    if (!sessionId || !rememberSpeakers) {
      return;
    }
    const displayName = speakerNames[speakerId]?.trim();
    if (!displayName) {
      setReviewError('Bitte zuerst einen lokalen Namen eingeben.');
      return;
    }
    runSpeakerAction(
      speakerId,
      () =>
        createManualSpeakerObservation(sessionId, {
          localSpeakerId: speakerId,
          displayName,
          observationId: suggestion?.observation_id,
        }),
      'Neues Profil wurde dauerhaft gemerkt.'
    );
  };

  const handleAssignExistingProfile = (speakerId: string, suggestion?: SpeakerObservation) => {
    if (!sessionId || !rememberSpeakers) {
      return;
    }
    const profileId = profileTargets[speakerId];
    if (!profileId) {
      setReviewError('Bitte ein gespeichertes Profil auswählen.');
      return;
    }
    runSpeakerAction(
      speakerId,
      () =>
        createManualSpeakerObservation(sessionId, {
          localSpeakerId: speakerId,
          profileId,
          observationId: suggestion?.observation_id,
        }),
      'Sprecher wurde einem bestehenden Profil zugeordnet.'
    );
  };

  const handleSessionOnly = async (speakerId: string, suggestion?: SpeakerObservation) => {
    if (suggestion && sessionId) {
      await handleRejectSuggestion(suggestion);
      return;
    }
    setActionMessage(
      speakerNames[speakerId]?.trim()
        ? 'Name bleibt nur in dieser Sitzung.'
        : 'Lokale Benennung bleibt leer und wird nicht dauerhaft gespeichert.'
    );
  };

  const handleMergeSpeaker = (sourceSpeaker: string) => {
    const targetSpeaker = mergeTargets[sourceSpeaker];
    if (!targetSpeaker || targetSpeaker === sourceSpeaker) {
      return;
    }

    setTranscript(
      transcript.map((line) =>
        line.speaker === sourceSpeaker ? { ...line, speaker: targetSpeaker } : line
      )
    );

    const nextNames = { ...speakerNames };
    if (!nextNames[targetSpeaker] && nextNames[sourceSpeaker]) {
      nextNames[targetSpeaker] = nextNames[sourceSpeaker];
    }
    delete nextNames[sourceSpeaker];
    setSpeakerNames(nextNames);

    const nextTargets = { ...mergeTargets };
    delete nextTargets[sourceSpeaker];
    setMergeTargets(nextTargets);
  };

  const handleProfileSelection = (profileId: string) => {
    setSelectedProfileId(profileId);
    const profile = profiles.find((item) => item.profile_id === profileId);
    setProfileNameDraft(profile?.display_name ?? '');
  };

  const handleRenameProfile = async () => {
    if (!selectedProfile || !profileNameDraft.trim()) {
      return;
    }
    setReviewError(null);
    try {
      const updated = await updateSpeakerProfile(selectedProfile.profile_id, {
        displayName: profileNameDraft.trim(),
      });
      setProfiles((current) =>
        current.map((profile) =>
          profile.profile_id === updated.profile_id ? updated : profile
        )
      );
      setActionMessage('Profil wurde umbenannt.');
    } catch (error) {
      setReviewError(
        error instanceof Error ? error.message : 'Profil konnte nicht umbenannt werden'
      );
    }
  };

  const handleArchiveProfile = async () => {
    if (!selectedProfile) {
      return;
    }
    setReviewError(null);
    try {
      await archiveSpeakerProfile(selectedProfile.profile_id);
      await refreshProfiles();
      setActionMessage('Profil wurde archiviert.');
    } catch (error) {
      setReviewError(
        error instanceof Error ? error.message : 'Profil konnte nicht archiviert werden'
      );
    }
  };

  const handleDeleteProfileEmbeddings = async () => {
    if (!selectedProfile) {
      return;
    }
    setReviewError(null);
    try {
      const result = await deleteSpeakerProfileEmbeddings(selectedProfile.profile_id);
      setActionMessage(
        result.deleted_count === 1
          ? 'Ein Embedding wurde gelöscht.'
          : `${result.deleted_count} Embeddings wurden gelöscht.`
      );
    } catch (error) {
      setReviewError(
        error instanceof Error ? error.message : 'Embeddings konnten nicht gelöscht werden'
      );
    }
  };

  const handleBackfillProfileEmbeddings = async () => {
    if (!selectedProfile) {
      return;
    }
    setReviewError(null);
    try {
      const result = await backfillSpeakerEmbeddings(selectedProfile.profile_id);
      await refreshProfiles();
      await refreshDiagnostics();
      setActionMessage(
        `${result.saved_embedding_count} Embeddings nachgeholt, ` +
          `${result.skipped_count} Zuordnungen übersprungen.`
      );
      if (result.errors.length > 0) {
        setReviewError(result.errors.join('; '));
      }
    } catch (error) {
      setReviewError(
        error instanceof Error
          ? error.message
          : 'Embeddings konnten nicht nachgeholt werden'
      );
    }
  };

  if (speakerInfo.length === 0) return null;

  return (
    <div className="bg-white rounded-lg border border-gray-200 overflow-hidden">
      <button
        type="button"
        onClick={() => setIsExpanded(!isExpanded)}
        className="w-full px-4 py-3 flex items-center justify-between bg-gray-50 hover:bg-gray-100 transition-colors"
      >
        <span className="font-medium text-gray-700">
          Sprecher umbenennen und Profile prüfen
          <span className="ml-2 text-sm font-normal text-gray-500">
            (optional)
          </span>
        </span>
        <span className="text-gray-400 text-lg">
          {isExpanded ? '▲' : '▼'}
        </span>
      </button>

      {isExpanded && (
        <div className="p-4 space-y-4 border-t border-gray-200">
          <div className="rounded-md border border-blue-200 bg-blue-50 px-3 py-2 text-sm text-blue-900">
            {rememberSpeakers
              ? 'Lokale Benennung wirkt nur in dieser Sitzung. Ein dauerhaftes Sprecherprofil wird erst gespeichert, wenn Sie einen Vorschlag übernehmen, ein neues Profil merken oder ein bestehendes Profil zuordnen.'
              : 'Lokale Benennung wirkt nur in dieser Sitzung. Dauerhafte Sprecherprofile sind ausgeschaltet und werden in diesem Durchlauf nicht vorgeschlagen oder gespeichert.'}
          </div>

          {sessionId && reviewStatus === 'loading' && (
            <div className="text-sm text-gray-600">Sprecherprofile werden geladen...</div>
          )}
          {reviewError && <div className="text-sm text-red-600">{reviewError}</div>}
          {actionMessage && (
            <div className="text-sm text-green-700">{actionMessage}</div>
          )}
          {audioUrl && (
            <AudioPlayer
              audioUrl={audioUrl}
              currentTime={sampleSeekTime}
              playOnSeek
            />
          )}

          {speakerInfo.map(({ id, sample, start }) => {
            const suggestion = suggestionsBySpeaker.get(id);
            const accepted = acceptedBySpeaker.get(id);
            const diagnostic = diagnosticsBySpeaker.get(id);
            const selectedTarget = profileTargets[id] || '';
            const currentName = speakerNames[id] || '';
            const isBusy = actionSpeaker === id;

            return (
              <div key={id} className="border border-gray-200 rounded-lg p-3 space-y-3">
                <div className="flex flex-wrap items-start gap-3">
                  <div className="w-28 flex-shrink-0">
                    <span className="text-sm font-mono text-gray-500">{id}</span>
                  </div>
                  <span className="text-gray-400 mt-1">→</span>
                  <div className="flex-1 min-w-64">
                    <input
                      type="text"
                      value={currentName}
                      onChange={(e) => handleNameChange(id, e.target.value)}
                      placeholder="Name eingeben..."
                      aria-label={`${id} lokal benennen`}
                      className="w-full px-3 py-1.5 text-sm border border-gray-300 rounded-md focus:ring-2 focus:ring-blue-500 focus:border-blue-500 outline-none"
                    />
                    <p className="mt-1 text-xs text-gray-400 italic truncate">
                      "{sample}"
                    </p>
                    {audioUrl && (
                      <button
                        type="button"
                        onClick={() => setSampleSeekTime(start)}
                        className="mt-2 rounded border border-blue-200 bg-blue-50 px-2 py-1 text-xs font-medium text-blue-700 hover:bg-blue-100"
                      >
                        Ausschnitt anhören
                      </button>
                    )}
                    {accepted && (
                      <div className="mt-1 flex flex-wrap items-center gap-2">
                        <p className="text-xs text-green-700">
                          Dauerhaft zugeordnet: {accepted.display_name}
                        </p>
                        <button
                          type="button"
                          onClick={() => handleUnassignAccepted(accepted)}
                          disabled={isBusy}
                          className="rounded border border-red-200 bg-red-50 px-2 py-1 text-xs font-medium text-red-700 hover:bg-red-100 disabled:opacity-50"
                        >
                          Zuordnung lösen
                        </button>
                      </div>
                    )}
                  </div>
                  {speakerInfo.length > 1 && (
                    <div className="w-full sm:w-64 flex gap-2">
                      <select
                        value={mergeTargets[id] || ''}
                        onChange={(event) =>
                          setMergeTargets({
                            ...mergeTargets,
                            [id]: event.target.value,
                          })
                        }
                        className="min-w-0 flex-1 px-2 py-1.5 text-sm border border-gray-300 rounded-md focus:ring-2 focus:ring-blue-500 focus:border-blue-500 outline-none"
                        aria-label={`${id} mit Sprecher zusammenführen`}
                      >
                        <option value="">Zusammenführen mit...</option>
                        {speakerInfo
                          .filter((speaker) => speaker.id !== id)
                          .map((speaker) => (
                            <option key={speaker.id} value={speaker.id}>
                              {speakerNames[speaker.id] || speaker.id}
                            </option>
                          ))}
                      </select>
                      <button
                        type="button"
                        onClick={() => handleMergeSpeaker(id)}
                        disabled={!mergeTargets[id]}
                        className="px-3 py-1.5 text-sm bg-gray-900 text-white rounded hover:bg-gray-700 disabled:bg-gray-200 disabled:text-gray-400"
                      >
                        Mergen
                      </button>
                    </div>
                  )}
                </div>

                {rememberSpeakers && (
                  <div className="pl-0 sm:pl-36 space-y-2">
                    {suggestion ? (
                      <div className="rounded-md border border-yellow-200 bg-yellow-50 p-3">
                        <div className="flex flex-wrap items-center justify-between gap-2">
                          <div>
                            <div className="text-sm font-medium text-gray-900">
                              Vorschlag: {suggestion.profile_display_name}
                            </div>
                            <div className="text-xs text-gray-600">
                              {formatConfidence(suggestion.confidence)}
                            </div>
                          </div>
                          <div className="flex flex-wrap gap-2">
                            <button
                              type="button"
                              onClick={() => handleConfirmSuggestion(id, suggestion)}
                              disabled={isBusy}
                              className="px-3 py-1.5 text-sm bg-green-600 text-white rounded hover:bg-green-700 disabled:opacity-50"
                            >
                              Vorschlag übernehmen
                            </button>
                            <button
                              type="button"
                              onClick={() => handleRejectSuggestion(suggestion)}
                              disabled={isBusy}
                              className="px-3 py-1.5 text-sm bg-white border border-gray-300 rounded hover:bg-gray-100 disabled:opacity-50"
                            >
                              Ablehnen
                            </button>
                          </div>
                        </div>
                      </div>
                    ) : (
                      <div className="text-xs text-gray-500">
                        Kein automatischer Profilvorschlag für diesen Sprecher.
                        {diagnostic && !accepted && (
                          <span className="ml-1">
                            Grund: {diagnostic.reason}
                            {diagnostic.best_score !== null &&
                              diagnostic.best_score !== undefined &&
                              diagnostic.suggest_threshold !== null &&
                              diagnostic.suggest_threshold !== undefined
                              ? ` (${formatConfidence(diagnostic.best_score)} unter ${formatConfidence(diagnostic.suggest_threshold)})`
                              : ''}
                          </span>
                        )}
                      </div>
                    )}

                    <div className="flex flex-wrap items-center gap-2">
                      <button
                        type="button"
                        onClick={() => handleRememberNewProfile(id, suggestion)}
                        disabled={!sessionId || !currentName.trim() || isBusy}
                        className="px-3 py-1.5 text-sm bg-blue-600 text-white rounded hover:bg-blue-700 disabled:bg-gray-200 disabled:text-gray-400"
                      >
                        Neues Profil merken
                      </button>
                      <select
                        value={selectedTarget}
                        onChange={(event) =>
                          setProfileTargets({
                            ...profileTargets,
                            [id]: event.target.value,
                          })
                        }
                        disabled={!sessionId || profiles.length === 0}
                        aria-label={`${id} bestehendem Profil zuordnen`}
                        className="px-2 py-1.5 text-sm border border-gray-300 rounded-md focus:ring-2 focus:ring-blue-500 focus:border-blue-500 outline-none disabled:bg-gray-100"
                      >
                        <option value="">Gespeichertes Profil auswählen...</option>
                        {profiles.map((profile) => (
                          <option key={profile.profile_id} value={profile.profile_id}>
                            {profile.display_name} ({profile.embedding_count ?? 0})
                          </option>
                        ))}
                      </select>
                      <button
                        type="button"
                        onClick={() => handleAssignExistingProfile(id, suggestion)}
                        disabled={!sessionId || !selectedTarget || isBusy}
                        className="px-3 py-1.5 text-sm bg-gray-900 text-white rounded hover:bg-gray-700 disabled:bg-gray-200 disabled:text-gray-400"
                      >
                        Bestehendem Profil zuordnen
                      </button>
                      <button
                        type="button"
                        onClick={() => handleSessionOnly(id, suggestion)}
                        disabled={isBusy}
                        className="px-3 py-1.5 text-sm bg-white border border-gray-300 rounded hover:bg-gray-100 disabled:opacity-50"
                      >
                        Nur in dieser Sitzung benennen
                      </button>
                    </div>
                  </div>
                )}
              </div>
            );
          })}

          {sessionId && rememberSpeakers && (
            <div className="border-t border-gray-200 pt-4">
              <h3 className="text-sm font-medium text-gray-900 mb-2">
                Gespeicherte Profile verwalten
              </h3>
              {profiles.length === 0 ? (
                <p className="text-sm text-gray-500">
                  Noch keine gespeicherten Sprecherprofile vorhanden.
                </p>
              ) : (
                <div className="flex flex-wrap gap-2 items-center">
                  <select
                    value={selectedProfileId}
                    onChange={(event) => handleProfileSelection(event.target.value)}
                    aria-label="Gespeichertes Profil auswählen"
                    className="px-2 py-1.5 text-sm border border-gray-300 rounded-md focus:ring-2 focus:ring-blue-500 focus:border-blue-500 outline-none"
                  >
                    {profiles.map((profile) => (
                      <option key={profile.profile_id} value={profile.profile_id}>
                        {profile.display_name} ({profile.embedding_count ?? 0})
                      </option>
                    ))}
                  </select>
                  <input
                    type="text"
                    value={profileNameDraft}
                    onChange={(event) => setProfileNameDraft(event.target.value)}
                    aria-label="Profil umbenennen"
                    className="px-3 py-1.5 text-sm border border-gray-300 rounded-md focus:ring-2 focus:ring-blue-500 focus:border-blue-500 outline-none"
                  />
                  <button
                    type="button"
                    onClick={handleRenameProfile}
                    disabled={!selectedProfile || !profileNameDraft.trim()}
                    className="px-3 py-1.5 text-sm bg-white border border-gray-300 rounded hover:bg-gray-100 disabled:opacity-50"
                  >
                    Profil umbenennen
                  </button>
                  <button
                    type="button"
                    onClick={handleArchiveProfile}
                    disabled={!selectedProfile}
                    className="px-3 py-1.5 text-sm bg-red-50 text-red-700 border border-red-200 rounded hover:bg-red-100 disabled:opacity-50"
                  >
                    Profil archivieren
                  </button>
                  <button
                    type="button"
                    onClick={handleDeleteProfileEmbeddings}
                    disabled={!selectedProfile}
                    className="px-3 py-1.5 text-sm bg-red-50 text-red-700 border border-red-200 rounded hover:bg-red-100 disabled:opacity-50"
                  >
                    Embeddings löschen
                  </button>
                  <button
                    type="button"
                    onClick={handleBackfillProfileEmbeddings}
                    disabled={!selectedProfile}
                    className="px-3 py-1.5 text-sm bg-white border border-gray-300 rounded hover:bg-gray-100 disabled:opacity-50"
                  >
                    Embeddings nachholen
                  </button>
                </div>
              )}
            </div>
          )}
        </div>
      )}
    </div>
  );
}

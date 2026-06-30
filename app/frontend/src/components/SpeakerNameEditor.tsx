import { useState, useMemo } from 'react';
import type { TranscriptLine } from '../types';

interface SpeakerNameEditorProps {
  transcript: TranscriptLine[];
  setTranscript: (transcript: TranscriptLine[]) => void;
  speakerNames: Record<string, string>;
  setSpeakerNames: (names: Record<string, string>) => void;
}

export default function SpeakerNameEditor({
  transcript,
  setTranscript,
  speakerNames,
  setSpeakerNames,
}: SpeakerNameEditorProps) {
  // Extract unique speakers and a sample of their text
  const speakerInfo = useMemo(() => {
    const speakers = new Map<string, string>();
    for (const line of transcript) {
      if (!speakers.has(line.speaker)) {
        // Store first text snippet as sample (truncate if too long)
        const sample = line.text.length > 60
          ? line.text.substring(0, 60) + '...'
          : line.text;
        speakers.set(line.speaker, sample);
      }
    }
    return Array.from(speakers.entries()).map(([id, sample]) => ({ id, sample }));
  }, [transcript]);

  // Auto-expand if 3 or fewer speakers
  const [isExpanded, setIsExpanded] = useState(speakerInfo.length <= 3);
  const [mergeTargets, setMergeTargets] = useState<Record<string, string>>({});

  const handleNameChange = (speakerId: string, name: string) => {
    setSpeakerNames({
      ...speakerNames,
      [speakerId]: name,
    });
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

  if (speakerInfo.length === 0) return null;

  return (
    <div className="bg-white rounded-lg border border-gray-200 overflow-hidden">
      <button
        onClick={() => setIsExpanded(!isExpanded)}
        className="w-full px-4 py-3 flex items-center justify-between bg-gray-50 hover:bg-gray-100 transition-colors"
      >
        <span className="font-medium text-gray-700">
          Sprecher umbenennen
          <span className="ml-2 text-sm font-normal text-gray-500">
            (optional)
          </span>
        </span>
        <span className="text-gray-400 text-lg">
          {isExpanded ? '▲' : '▼'}
        </span>
      </button>

      {isExpanded && (
        <div className="p-4 space-y-3 border-t border-gray-200">
          {speakerInfo.map(({ id, sample }) => (
            <div key={id} className="flex flex-wrap items-start gap-3">
              <div className="w-28 flex-shrink-0">
                <span className="text-sm font-mono text-gray-500">{id}</span>
              </div>
              <span className="text-gray-400 mt-1">→</span>
              <div className="flex-1">
                <input
                  type="text"
                  value={speakerNames[id] || ''}
                  onChange={(e) => handleNameChange(id, e.target.value)}
                  placeholder="Name eingeben..."
                  className="w-full px-3 py-1.5 text-sm border border-gray-300 rounded-md focus:ring-2 focus:ring-blue-500 focus:border-blue-500 outline-none"
                />
                <p className="mt-1 text-xs text-gray-400 italic truncate">
                  "{sample}"
                </p>
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
          ))}
        </div>
      )}
    </div>
  );
}

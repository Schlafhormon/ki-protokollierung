import { useState, useRef, useEffect, type MouseEvent } from 'react';
import type { AssignmentStepProps, TopColor } from '../types';
import AudioPlayer from './AudioPlayer';
import { useAudioSync } from '../hooks/useAudioSync';
import SpeakerNameEditor from './SpeakerNameEditor';

function formatTime(seconds: number): string {
  const mins = Math.floor(seconds / 60);
  const secs = Math.floor(seconds % 60);
  return `${mins}:${secs.toString().padStart(2, '0')}`;
}

// Color palette for TOPs
const topColors: TopColor[] = [
  { bg: 'bg-blue-100', border: 'border-blue-300', text: 'text-blue-700', dot: 'bg-blue-500' },
  { bg: 'bg-green-100', border: 'border-green-300', text: 'text-green-700', dot: 'bg-green-500' },
  { bg: 'bg-yellow-100', border: 'border-yellow-300', text: 'text-yellow-700', dot: 'bg-yellow-500' },
  { bg: 'bg-purple-100', border: 'border-purple-300', text: 'text-purple-700', dot: 'bg-purple-500' },
  { bg: 'bg-pink-100', border: 'border-pink-300', text: 'text-pink-700', dot: 'bg-pink-500' },
  { bg: 'bg-indigo-100', border: 'border-indigo-300', text: 'text-indigo-700', dot: 'bg-indigo-500' },
  { bg: 'bg-red-100', border: 'border-red-300', text: 'text-red-700', dot: 'bg-red-500' },
  { bg: 'bg-orange-100', border: 'border-orange-300', text: 'text-orange-700', dot: 'bg-orange-500' },
];

export default function AssignmentStep({
  onNext,
  onBack,
  tops,
  transcript,
  setTranscript,
  assignments,
  setAssignments,
  audioUrl,
  speakerNames,
  setSpeakerNames,
}: AssignmentStepProps) {
  const [selectedTop, setSelectedTop] = useState(0);
  const [selectionStart, setSelectionStart] = useState<number | null>(null);
  const [editingLine, setEditingLine] = useState<number | null>(null);
  const [editText, setEditText] = useState('');
  const transcriptContainerRef = useRef<HTMLDivElement>(null);

  // Audio sync hook
  const {
    seekTime,
    currentLineIndex,
    handleTimeUpdate,
    seekToLine,
    isAutoScroll,
  } = useAudioSync(transcript);

  // Auto-scroll to current line during playback
  useEffect(() => {
    if (isAutoScroll && currentLineIndex >= 0 && transcriptContainerRef.current) {
      const lineElement = transcriptContainerRef.current.children[currentLineIndex] as HTMLElement;
      if (lineElement) {
        lineElement.scrollIntoView({ behavior: 'smooth', block: 'center' });
      }
    }
  }, [currentLineIndex, isAutoScroll]);

  // Helper to get display name for a speaker
  const getDisplayName = (speakerId: string) => speakerNames[speakerId] || speakerId;

  const getColor = (topIndex: number): TopColor => topColors[topIndex % topColors.length]!;

  const getAssignmentCounts = (): Record<number, number> => {
    const counts: Record<number, number> = {};
    tops.forEach((_, index) => {
      counts[index] = assignments.filter((a) => a === index).length;
    });
    return counts;
  };

  const counts = getAssignmentCounts();

  const handleLineClick = (lineIndex: number, event: MouseEvent<HTMLDivElement>) => {
    if (editingLine === lineIndex) {
      return;
    }

    // Double-click to seek audio
    const line = transcript[lineIndex];
    if (event.detail === 2 && audioUrl && line) {
      seekToLine(lineIndex, line);
      return;
    }

    if (event.shiftKey && selectionStart !== null) {
      // Range selection
      const start = Math.min(selectionStart, lineIndex);
      const end = Math.max(selectionStart, lineIndex);
      const newAssignments = [...assignments];
      for (let i = start; i <= end; i++) {
        newAssignments[i] = selectedTop;
      }
      setAssignments(newAssignments);
      setSelectionStart(null);
    } else {
      // Single click - toggle or set
      const newAssignments = [...assignments];
      if (newAssignments[lineIndex] === selectedTop) {
        newAssignments[lineIndex] = null; // Unassign
      } else {
        newAssignments[lineIndex] = selectedTop;
      }
      setAssignments(newAssignments);
      setSelectionStart(lineIndex);
    }
  };

  const startLineEdit = (lineIndex: number, text: string) => {
    setEditingLine(lineIndex);
    setEditText(text);
  };

  const saveLineEdit = () => {
    if (editingLine === null) {
      return;
    }
    const updatedTranscript = transcript.map((line, index) =>
      index === editingLine ? { ...line, text: editText } : line
    );
    setTranscript(updatedTranscript);
    setEditingLine(null);
    setEditText('');
  };

  const cancelLineEdit = () => {
    setEditingLine(null);
    setEditText('');
  };

  const assignedCount = assignments.filter((a) => a !== null).length;
  const totalCount = transcript.length;
  const canProceed = assignedCount > 0;

  return (
    <div className="space-y-6">
      {/* Instructions */}
      <div className="bg-blue-50 border border-blue-200 rounded-lg p-4">
        <p className="text-blue-800 text-sm">
          <strong>Anleitung:</strong> 1) TOP links auswählen → 2) Zeilen rechts
          anklicken (Shift+Klick für Bereich) → 3) Zugeordnete Zeilen werden
          farblich markiert{audioUrl && ' → Doppelklick auf Zeile zum Abspielen'}
        </p>
      </div>

      {/* Speaker Name Editor */}
      <SpeakerNameEditor
        transcript={transcript}
        speakerNames={speakerNames}
        setSpeakerNames={setSpeakerNames}
      />

      {/* Progress */}
      <div className="flex items-center justify-between text-sm text-gray-600">
        <span>
          {assignedCount} von {totalCount} Zeilen zugeordnet
        </span>
        <div className="w-48 h-2 bg-gray-200 rounded-full overflow-hidden">
          <div
            className="h-full bg-blue-500 transition-all"
            style={{ width: `${(assignedCount / totalCount) * 100}%` }}
          />
        </div>
      </div>

      {/* Main Layout */}
      <div className="flex gap-6 h-[600px]">
        {/* TOPs Sidebar */}
        <div className="w-72 bg-white rounded-lg border border-gray-200 p-4 overflow-y-auto">
          <h3 className="font-medium text-gray-900 mb-4">Tagesordnung</h3>
          <div className="space-y-2">
            {tops.map((top, index) => {
              const color = getColor(index);
              const isSelected = selectedTop === index;
              return (
                <button
                  key={index}
                  onClick={() => setSelectedTop(index)}
                  className={`w-full text-left px-3 py-3 rounded-lg border-2 transition-all ${
                    isSelected
                      ? `${color.bg} ${color.border} ${color.text}`
                      : 'border-transparent hover:bg-gray-50'
                  }`}
                >
                  <div className="flex items-start gap-2">
                    <div className={`w-3 h-3 rounded-full mt-1 ${color.dot}`} />
                    <div className="flex-1 min-w-0">
                      <div
                        className="font-medium text-sm truncate"
                        title={top || `TOP ${index + 1}`}
                      >
                        {index + 1}. {top || `TOP ${index + 1}`}
                      </div>
                      <div className="text-xs text-gray-500 mt-1">
                        {counts[index]} Zeilen
                      </div>
                    </div>
                  </div>
                </button>
              );
            })}
          </div>
        </div>

        {/* Transcript */}
        <div className="flex-1 bg-white rounded-lg border border-gray-200 overflow-hidden flex flex-col">
          {/* Audio Player */}
          {audioUrl && (
            <div className="px-4 py-3 border-b border-gray-200 bg-gray-50">
              <AudioPlayer
                audioUrl={audioUrl}
                currentTime={seekTime}
                onTimeUpdate={handleTimeUpdate}
              />
            </div>
          )}

          <div className="px-4 py-3 border-b border-gray-200 bg-gray-50">
            <h3 className="font-medium text-gray-900">Transkript</h3>
          </div>
          <div ref={transcriptContainerRef} className="flex-1 overflow-y-auto p-2">
            {transcript.map((line, index) => {
              const assignedTo = assignments[index] ?? null;
              const color = assignedTo !== null ? getColor(assignedTo) : null;
              const isCurrentLine = index === currentLineIndex;
              return (
                <div
                  key={index}
                  onClick={(e) => handleLineClick(index, e)}
                  className={`px-3 py-2 rounded cursor-pointer transition-colors text-sm border-l-4 mb-1 ${
                    color
                      ? `${color.bg} ${color.border} hover:opacity-80`
                      : 'border-transparent hover:bg-gray-100'
                  } ${isCurrentLine ? 'ring-2 ring-blue-500 ring-offset-1' : ''}`}
                >
                  <span className="font-medium text-gray-600">
                    {getDisplayName(line.speaker)}:
                  </span>{' '}
                  {editingLine === index ? (
                    <div
                      className="mt-2 space-y-2"
                      onClick={(event) => event.stopPropagation()}
                    >
                      <textarea
                        value={editText}
                        onChange={(event) => setEditText(event.target.value)}
                        className="w-full min-h-20 p-2 border border-gray-300 rounded-md focus:outline-none focus:ring-2 focus:ring-blue-500"
                        aria-label={`Transkriptzeile ${index + 1} korrigieren`}
                      />
                      <div className="flex gap-2">
                        <button
                          type="button"
                          onClick={saveLineEdit}
                          className="px-3 py-1 text-xs bg-green-600 text-white rounded hover:bg-green-700"
                        >
                          Speichern
                        </button>
                        <button
                          type="button"
                          onClick={cancelLineEdit}
                          className="px-3 py-1 text-xs bg-gray-200 text-gray-700 rounded hover:bg-gray-300"
                        >
                          Abbrechen
                        </button>
                      </div>
                    </div>
                  ) : (
                    <>
                      <span className="text-gray-800">{line.text}</span>
                      <button
                        type="button"
                        onClick={(event) => {
                          event.stopPropagation();
                          startLineEdit(index, line.text);
                        }}
                        className="ml-2 text-xs text-gray-500 hover:text-blue-600"
                      >
                        Bearbeiten
                      </button>
                    </>
                  )}
                  <span className="ml-2 text-xs text-gray-400">
                    [{formatTime(line.start)}]
                  </span>
                </div>
              );
            })}
          </div>
        </div>
      </div>

      {/* Actions */}
      <div className="flex justify-between">
        <button
          onClick={onBack}
          className="px-6 py-3 rounded-lg font-medium text-gray-600 hover:bg-gray-100 transition-colors flex items-center gap-2"
        >
          <span>←</span>
          Zurück
        </button>
        <button
          onClick={onNext}
          disabled={!canProceed}
          className={`px-6 py-3 rounded-lg font-medium transition-colors flex items-center gap-2 ${
            canProceed
              ? 'bg-blue-600 text-white hover:bg-blue-700'
              : 'bg-gray-200 text-gray-400 cursor-not-allowed'
          }`}
        >
          Zusammenfassungen erstellen
          <span>→</span>
        </button>
      </div>
    </div>
  );
}

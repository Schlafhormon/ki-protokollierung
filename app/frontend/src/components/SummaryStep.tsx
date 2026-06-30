import { useState, useRef, useEffect, useCallback, type ChangeEvent } from 'react';
import type { StructuredSummary, SummaryStepProps, TranscriptLine } from '../types';
import AudioPlayer from './AudioPlayer';
import { useAudioSync } from '../hooks/useAudioSync';

function formatTime(seconds: number): string {
  const mins = Math.floor(seconds / 60);
  const secs = Math.floor(seconds % 60);
  return `${mins}:${secs.toString().padStart(2, '0')}`;
}

const SUMMARY_SECTIONS: Array<{
  key: keyof StructuredSummary;
  label: string;
  important?: boolean;
}> = [
  { key: 'discussion', label: 'Diskussion' },
  { key: 'decisions', label: 'Beschluss', important: true },
  { key: 'votes', label: 'Abstimmung', important: true },
  { key: 'action_items', label: 'Maßnahmen', important: true },
  { key: 'open_points', label: 'Offene Punkte' },
  { key: 'uncertainties', label: 'Unsicherheiten' },
];

export default function SummaryStep({
  onBack,
  tops,
  transcript,
  assignments,
  summaries,
  setSummaries,
  summaryReviews = {},
  onRegenerateSummary,
  isGenerating,
  audioUrl,
  speakerNames,
}: SummaryStepProps) {
  const [selectedTop, setSelectedTop] = useState(0);
  const [editingTop, setEditingTop] = useState<number | null>(null);
  const [editText, setEditText] = useState('');
  const [copied, setCopied] = useState(false);
  const [activeSourceLine, setActiveSourceLine] = useState<number | null>(null);
  const transcriptContainerRef = useRef<HTMLDivElement>(null);
  const transcriptLineRefs = useRef<Array<HTMLDivElement | null>>([]);

  // Audio sync hook (uses full transcript for seeking)
  const {
    seekTime,
    currentLineIndex,
    handleTimeUpdate,
    seekToLine,
    isAutoScroll,
  } = useAudioSync(transcript);

  const getTranscriptForTop = useCallback((topIndex: number) => {
    return transcript.filter((_, i) => assignments[i] === topIndex);
  }, [assignments, transcript]);

  useEffect(() => {
    setActiveSourceLine(null);
    transcriptLineRefs.current = [];
  }, [selectedTop]);

  // Auto-scroll to current line during playback (within filtered transcript)
  useEffect(() => {
    if (isAutoScroll && currentLineIndex >= 0 && transcriptContainerRef.current) {
      // Find position of current line within the filtered transcript
      const topLines = getTranscriptForTop(selectedTop);
      const filteredIndex = topLines.findIndex((line) => {
        const originalIndex = transcript.indexOf(line);
        return originalIndex === currentLineIndex;
      });
      if (filteredIndex >= 0) {
        const lineElement = transcriptContainerRef.current.children[filteredIndex] as HTMLElement;
        if (lineElement) {
          lineElement.scrollIntoView({ behavior: 'smooth', block: 'center' });
        }
      }
    }
  }, [currentLineIndex, getTranscriptForTop, isAutoScroll, selectedTop, transcript]);

  // Helper to get display name for a speaker
  const getDisplayName = (speakerId: string) => speakerNames[speakerId] || speakerId;

  const handleLineDoubleClick = (line: TranscriptLine) => {
    if (audioUrl) {
      const originalIndex = transcript.indexOf(line);
      if (originalIndex >= 0) {
        seekToLine(originalIndex, line);
      }
    }
  };

  const jumpToTranscriptLine = (localLineIndex: number | null | undefined) => {
    if (localLineIndex === null || localLineIndex === undefined) return;
    const line = topLines[localLineIndex];
    if (!line) return;

    setActiveSourceLine(localLineIndex);
    transcriptLineRefs.current[localLineIndex]?.scrollIntoView({
      behavior: 'smooth',
      block: 'center',
    });

    if (audioUrl) {
      const originalIndex = transcript.indexOf(line);
      if (originalIndex >= 0) {
        seekToLine(originalIndex, line);
      }
    }
  };

  const startEditing = (topIndex: number) => {
    setEditingTop(topIndex);
    setEditText(summaries[topIndex] || '');
  };

  const saveEdit = () => {
    if (editingTop !== null) {
      const newSummaries = { ...summaries };
      newSummaries[editingTop] = editText;
      setSummaries(newSummaries);
      setEditingTop(null);
    }
  };

  const cancelEdit = () => {
    setEditingTop(null);
    setEditText('');
  };

  const handleCopy = async () => {
    const text = summaries[selectedTop];
    if (text) {
      await navigator.clipboard.writeText(text);
      setCopied(true);
      setTimeout(() => setCopied(false), 2000);
    }
  };

  const handleExport = () => {
    // Generate the full protocol text
    let content = 'SITZUNGSPROTOKOLL\n';
    content += '='.repeat(50) + '\n\n';

    tops.forEach((top, index) => {
      content += `TOP ${index + 1}: ${top}\n`;
      content += '-'.repeat(40) + '\n';
      content += (summaries[index] || 'Keine Zusammenfassung vorhanden.') + '\n\n';
    });

    // Create and download file
    const blob = new Blob([content], { type: 'text/plain;charset=utf-8' });
    const url = URL.createObjectURL(blob);
    const a = document.createElement('a');
    a.href = url;
    a.download = 'protokoll.txt';
    document.body.appendChild(a);
    a.click();
    document.body.removeChild(a);
    URL.revokeObjectURL(url);
  };

  const topLines = getTranscriptForTop(selectedTop);
  const selectedReview = summaryReviews[selectedTop];
  const selectedWarnings = selectedReview?.review_warnings ?? [];
  const structured = selectedReview?.structured ?? null;

  const getSourceLink = (section: keyof StructuredSummary, itemIndex: number) => {
    return selectedReview?.source_links.find(
      (link) => link.section === section && link.item_index === itemIndex
    );
  };

  const hasStructuredItems = Boolean(
    structured &&
      SUMMARY_SECTIONS.some((section) => structured[section.key]?.length)
  );

  return (
    <div className="space-y-6">
      {/* Main Layout */}
      <div className="flex gap-6 h-[600px]">
        {/* TOPs Sidebar */}
        <div className="w-72 bg-white rounded-lg border border-gray-200 p-4 overflow-y-auto">
          <h3 className="font-medium text-gray-900 mb-4">Tagesordnung</h3>
          <div className="space-y-2">
            {tops.map((top, index) => {
              const isSelected = selectedTop === index;
              const hasSummary = summaries[index] && summaries[index].trim();
              return (
                <button
                  key={index}
                  onClick={() => setSelectedTop(index)}
                  className={`w-full text-left px-3 py-3 rounded-lg border-2 transition-all ${
                    isSelected
                      ? 'bg-blue-50 border-blue-300 text-blue-700'
                      : 'border-transparent hover:bg-gray-50'
                  }`}
                >
                  <div className="flex items-start gap-2">
                    <div
                      className={`w-3 h-3 rounded-full mt-1 ${
                        hasSummary ? 'bg-green-500' : 'bg-gray-300'
                      }`}
                    />
                    <div className="flex-1 min-w-0">
                      <div
                        className="font-medium text-sm truncate"
                        title={top || `TOP ${index + 1}`}
                      >
                        {index + 1}. {top || `TOP ${index + 1}`}
                      </div>
                    </div>
                  </div>
                </button>
              );
            })}
          </div>
        </div>

        {/* Summary Content */}
        <div className="flex-1 flex flex-col gap-4">
          {/* Summary Box */}
          <div className="flex-1 bg-white rounded-lg border border-gray-200 overflow-hidden flex flex-col">
            <div className="px-4 py-3 border-b border-gray-200 bg-gray-50 flex items-center justify-between">
              <h3 className="font-medium text-gray-900">
                TOP {selectedTop + 1}: {tops[selectedTop]}
              </h3>
              <div className="flex gap-2">
                {editingTop === selectedTop ? (
                  <>
                    <button
                      onClick={saveEdit}
                      className="px-3 py-1 text-sm bg-green-500 text-white rounded hover:bg-green-600"
                    >
                      Speichern
                    </button>
                    <button
                      onClick={cancelEdit}
                      className="px-3 py-1 text-sm bg-gray-200 text-gray-700 rounded hover:bg-gray-300"
                    >
                      Abbrechen
                    </button>
                  </>
                ) : (
                  <>
                    <button
                      onClick={handleCopy}
                      disabled={!summaries[selectedTop]}
                      className="p-2 text-gray-600 hover:bg-gray-200 rounded disabled:opacity-50 disabled:cursor-not-allowed"
                      title={copied ? 'Kopiert!' : 'In Zwischenablage kopieren'}
                    >
                      {copied ? (
                        <svg className="w-4 h-4 text-green-600" fill="none" stroke="currentColor" viewBox="0 0 24 24">
                          <path strokeLinecap="round" strokeLinejoin="round" strokeWidth={2} d="M5 13l4 4L19 7" />
                        </svg>
                      ) : (
                        <svg className="w-4 h-4" fill="none" stroke="currentColor" viewBox="0 0 24 24">
                          <path strokeLinecap="round" strokeLinejoin="round" strokeWidth={2} d="M8 16H6a2 2 0 01-2-2V6a2 2 0 012-2h8a2 2 0 012 2v2m-6 12h8a2 2 0 002-2v-8a2 2 0 00-2-2h-8a2 2 0 00-2 2v8a2 2 0 002 2z" />
                        </svg>
                      )}
                    </button>
                    <button
                      onClick={() => startEditing(selectedTop)}
                      className="p-2 text-gray-600 hover:bg-gray-200 rounded"
                      title="Bearbeiten"
                    >
                      <svg className="w-4 h-4" fill="none" stroke="currentColor" viewBox="0 0 24 24">
                        <path strokeLinecap="round" strokeLinejoin="round" strokeWidth={2} d="M11 5H6a2 2 0 00-2 2v11a2 2 0 002 2h11a2 2 0 002-2v-5m-1.414-9.414a2 2 0 112.828 2.828L11.828 15H9v-2.828l8.586-8.586z" />
                      </svg>
                    </button>
                    <button
                      onClick={() => onRegenerateSummary(selectedTop)}
                      disabled={isGenerating}
                      className="p-2 text-blue-600 hover:bg-blue-100 rounded disabled:opacity-50"
                      title="Neu generieren"
                    >
                      <svg className={`w-4 h-4 ${isGenerating ? 'animate-spin' : ''}`} fill="none" stroke="currentColor" viewBox="0 0 24 24">
                        <path strokeLinecap="round" strokeLinejoin="round" strokeWidth={2} d="M4 4v5h.582m15.356 2A8.001 8.001 0 004.582 9m0 0H9m11 11v-5h-.581m0 0a8.003 8.003 0 01-15.357-2m15.357 2H15" />
                      </svg>
                    </button>
                  </>
                )}
              </div>
            </div>
            <div className="flex-1 p-4 overflow-y-auto">
              {editingTop === selectedTop ? (
                <textarea
                  value={editText}
                  onChange={(e: ChangeEvent<HTMLTextAreaElement>) => setEditText(e.target.value)}
                  className="w-full h-full p-3 border border-gray-300 rounded-lg focus:outline-none focus:ring-2 focus:ring-blue-500 resize-none"
                />
              ) : isGenerating ? (
                <div className="flex items-center justify-center h-full text-gray-500">
                  <div className="animate-spin mr-2">⏳</div>
                  Zusammenfassung wird generiert...
                </div>
              ) : summaries[selectedTop] ? (
                <div className="space-y-3">
                  {selectedWarnings.length > 0 && (
                    <div className="rounded-md border border-yellow-200 bg-yellow-50 px-3 py-2 text-sm text-yellow-900">
                      <div className="font-medium">Prüfhinweise</div>
                      <div className="mt-1 space-y-1">
                        {selectedWarnings.slice(0, 3).map((warning, index) => (
                          <button
                            key={`${warning.kind}-${warning.keyword ?? ''}-${index}`}
                            type="button"
                            onClick={() => jumpToTranscriptLine(warning.line_indices?.[0])}
                            className="block w-full text-left hover:underline disabled:no-underline"
                            disabled={!warning.line_indices?.length}
                            title={warning.excerpt || warning.message}
                          >
                            {warning.message}
                          </button>
                        ))}
                        {selectedWarnings.length > 3 && (
                          <div className="text-yellow-800">
                            +{selectedWarnings.length - 3} weitere Hinweise
                          </div>
                        )}
                      </div>
                    </div>
                  )}

                  {hasStructuredItems && structured ? (
                    <div className="space-y-3">
                      {SUMMARY_SECTIONS.map((section) => {
                        const items = structured[section.key] ?? [];
                        if (items.length === 0) return null;

                        return (
                          <section
                            key={section.key}
                            className={`border-l-4 pl-3 ${
                              section.important
                                ? 'border-blue-500'
                                : section.key === 'uncertainties'
                                  ? 'border-yellow-400'
                                  : 'border-gray-200'
                            }`}
                          >
                            <h4
                              className={`mb-2 text-sm font-semibold ${
                                section.important ? 'text-blue-900' : 'text-gray-800'
                              }`}
                            >
                              {section.label}
                            </h4>
                            <ul className="space-y-2">
                              {items.map((item, itemIndex) => {
                                const source = getSourceLink(section.key, itemIndex);
                                return (
                                  <li
                                    key={`${section.key}-${itemIndex}`}
                                    className="text-sm text-gray-700"
                                  >
                                    <div>{item}</div>
                                    <div className="mt-1 flex flex-wrap gap-2">
                                      {source?.missing_source ? (
                                        <span className="rounded border border-yellow-300 bg-yellow-50 px-2 py-0.5 text-xs font-medium text-yellow-800">
                                          Quelle fehlt
                                        </span>
                                      ) : source?.line_indices?.length ? (
                                        <button
                                          type="button"
                                          onClick={() => jumpToTranscriptLine(source.line_indices[0])}
                                          className="rounded border border-blue-200 bg-blue-50 px-2 py-0.5 text-xs font-medium text-blue-700 hover:bg-blue-100"
                                          title={source.excerpt || 'Transkriptstelle anzeigen'}
                                        >
                                          Beleg {source.start != null ? formatTime(source.start) : ''}
                                        </button>
                                      ) : null}
                                    </div>
                                  </li>
                                );
                              })}
                            </ul>
                          </section>
                        );
                      })}
                    </div>
                  ) : (
                    <div className="prose max-w-none text-gray-700 whitespace-pre-wrap">
                      {summaries[selectedTop]}
                    </div>
                  )}
                </div>
              ) : (
                <div className="flex items-center justify-center h-full text-gray-400">
                  Keine Zusammenfassung vorhanden. Klicken Sie auf "Neu generieren".
                </div>
              )}
            </div>
          </div>

          {/* Original Transcript for this TOP */}
          <div className="h-64 bg-white rounded-lg border border-gray-200 overflow-hidden flex flex-col">
            {/* Audio Player */}
            {audioUrl && (
              <div className="px-4 py-2 border-b border-gray-200 bg-gray-50">
                <AudioPlayer
                  audioUrl={audioUrl}
                  currentTime={seekTime}
                  onTimeUpdate={handleTimeUpdate}
                />
              </div>
            )}

            <div className="px-4 py-2 border-b border-gray-200 bg-gray-50">
              <h4 className="text-sm font-medium text-gray-700">
                Originaltranskript ({topLines.length} Zeilen)
                {audioUrl && <span className="text-gray-400 font-normal"> - Doppelklick zum Abspielen</span>}
              </h4>
            </div>
            <div ref={transcriptContainerRef} className="flex-1 overflow-y-auto p-3 text-sm">
              {topLines.length > 0 ? (
                topLines.map((line, index) => {
                  const originalIndex = transcript.indexOf(line);
                  const isCurrentLine = originalIndex === currentLineIndex;
                  return (
                    <div
                      key={index}
                      ref={(element) => {
                        transcriptLineRefs.current[index] = element;
                      }}
                      onDoubleClick={() => handleLineDoubleClick(line)}
                      className={`mb-1 px-2 py-1 rounded cursor-pointer hover:bg-gray-100 ${
                        isCurrentLine || activeSourceLine === index
                          ? 'ring-2 ring-blue-500 ring-offset-1 bg-blue-50'
                          : ''
                      }`}
                    >
                      <span className="font-medium text-gray-500">
                        {getDisplayName(line.speaker)}:
                      </span>{' '}
                      <span className="text-gray-700">{line.text}</span>
                      <span className="ml-2 text-xs text-gray-400">
                        [{formatTime(line.start)}]
                      </span>
                    </div>
                  );
                })
              ) : (
                <div className="text-gray-400 text-center py-4">
                  Keine Zeilen diesem TOP zugeordnet.
                </div>
              )}
            </div>
          </div>
        </div>
      </div>

      {/* Export Section */}
      <div className="bg-white rounded-lg border border-gray-200 p-4">
        <div className="flex items-center justify-between">
          <div>
            <h3 className="font-medium text-gray-900">Export</h3>
            <p className="text-sm text-gray-500">
              Protokoll als Datei herunterladen
            </p>
          </div>
          <div className="flex gap-3">
            <button
              onClick={handleExport}
              className="px-4 py-2 bg-gray-100 text-gray-700 rounded-lg hover:bg-gray-200 flex items-center gap-2"
            >
              📄 Text (.txt)
            </button>
          </div>
        </div>
      </div>

      {/* Actions */}
      <div className="flex justify-start">
        <button
          onClick={onBack}
          className="px-6 py-3 rounded-lg font-medium text-gray-600 hover:bg-gray-100 transition-colors flex items-center gap-2"
        >
          <span>←</span>
          Zurück zur Zuordnung
        </button>
      </div>
    </div>
  );
}

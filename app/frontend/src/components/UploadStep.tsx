import { useState, useRef, type DragEvent, type ChangeEvent } from 'react';
import type { UploadStepProps } from '../types';
import { extractTOPsFromPDF } from '../api';

export default function UploadStep({
  onNext,
  audioFile,
  setAudioFile,
  tops,
  setTops,
  llmSettings,
  telemetryOptIn,
  setTelemetryOptIn,
}: UploadStepProps) {
  const audioInputRef = useRef<HTMLInputElement>(null);
  const pdfInputRef = useRef<HTMLInputElement>(null);
  const [dragActive, setDragActive] = useState(false);
  const [pdfDragActive, setPdfDragActive] = useState(false);

  // PDF extraction state
  const [isExtractingTops, setIsExtractingTops] = useState(false);
  const [extractionError, setExtractionError] = useState<string | null>(null);
  const [extractedCount, setExtractedCount] = useState<number | null>(null);

  // Audio drag handlers
  const handleDrag = (e: DragEvent<HTMLDivElement>) => {
    e.preventDefault();
    e.stopPropagation();
    if (e.type === 'dragenter' || e.type === 'dragover') {
      setDragActive(true);
    } else if (e.type === 'dragleave') {
      setDragActive(false);
    }
  };

  const handleDrop = (e: DragEvent<HTMLDivElement>) => {
    e.preventDefault();
    e.stopPropagation();
    setDragActive(false);
    if (e.dataTransfer.files && e.dataTransfer.files[0]) {
      const file = e.dataTransfer.files[0];
      if (file.type.startsWith('audio/') || file.name.match(/\.(mp3|wav|m4a)$/i)) {
        setAudioFile(file);
      }
    }
  };

  const handleFileChange = (e: ChangeEvent<HTMLInputElement>) => {
    if (e.target.files && e.target.files[0]) {
      setAudioFile(e.target.files[0]);
    }
  };

  // PDF drag handlers
  const handlePdfDrag = (e: DragEvent<HTMLDivElement>) => {
    e.preventDefault();
    e.stopPropagation();
    if (e.type === 'dragenter' || e.type === 'dragover') {
      setPdfDragActive(true);
    } else if (e.type === 'dragleave') {
      setPdfDragActive(false);
    }
  };

  const handlePdfDrop = async (e: DragEvent<HTMLDivElement>) => {
    e.preventDefault();
    e.stopPropagation();
    setPdfDragActive(false);
    if (e.dataTransfer.files && e.dataTransfer.files[0]) {
      const file = e.dataTransfer.files[0];
      if (file.type === 'application/pdf' || file.name.endsWith('.pdf')) {
        await extractTopsFromFile(file);
      }
    }
  };

  const handlePdfFileChange = async (e: ChangeEvent<HTMLInputElement>) => {
    if (e.target.files && e.target.files[0]) {
      await extractTopsFromFile(e.target.files[0]);
    }
    // Reset the input so the same file can be selected again
    e.target.value = '';
  };

  const extractTopsFromFile = async (file: File) => {
    setIsExtractingTops(true);
    setExtractionError(null);
    setExtractedCount(null);

    try {
      const extractedTops = await extractTOPsFromPDF(file, {
        model: llmSettings?.model,
      });

      if (extractedTops.length > 0) {
        // Replace empty TOPs with extracted ones, but keep user-entered ones
        setTops(extractedTops);
        setExtractedCount(extractedTops.length);
      } else {
        setExtractionError('Keine TOPs im PDF gefunden. Bitte manuell eingeben.');
      }
    } catch (error) {
      const errorMessage = error instanceof Error ? error.message : 'Unbekannter Fehler';
      setExtractionError(errorMessage);
    } finally {
      setIsExtractingTops(false);
    }
  };

  // TOP management
  const addTop = () => {
    setTops([...tops, '']);
    setExtractedCount(null); // Clear success message when user modifies
  };

  const updateTop = (index: number, value: string) => {
    const newTops = [...tops];
    newTops[index] = value;
    setTops(newTops);
    setExtractedCount(null); // Clear success message when user modifies
  };

  const removeTop = (index: number) => {
    if (tops.length > 1) {
      setTops(tops.filter((_, i) => i !== index));
    } else {
      setTops(['']);
    }
    setExtractedCount(null); // Clear success message when user modifies
  };

  const clearAllTops = () => {
    setTops(['']);
    setExtractedCount(null);
    setExtractionError(null);
  };

  const canProceed = !!audioFile;

  return (
    <div className="space-y-8">
      {/* Audio Upload Section */}
      <div className="bg-white rounded-lg border border-gray-200 p-6">
        <h2 className="text-lg font-medium text-gray-900 mb-4 flex items-center gap-2">
          <span className="text-xl">🎙️</span>
          Audioaufnahme
        </h2>

        <div
          className={`border-2 border-dashed rounded-lg p-8 text-center transition-colors ${
            dragActive
              ? 'border-blue-500 bg-blue-50'
              : 'border-gray-300 hover:border-gray-400'
          }`}
          onDragEnter={handleDrag}
          onDragLeave={handleDrag}
          onDragOver={handleDrag}
          onDrop={handleDrop}
          onClick={() => audioInputRef.current?.click()}
        >
          <input
            ref={audioInputRef}
            type="file"
            accept="audio/*"
            onChange={handleFileChange}
            className="hidden"
          />

          {audioFile ? (
            <div className="flex items-center justify-center gap-3">
              <span className="text-green-500 text-2xl">✓</span>
              <span className="text-gray-700 font-medium">{audioFile.name}</span>
              <span className="text-gray-500">
                ({(audioFile.size / 1024 / 1024).toFixed(1)} MB)
              </span>
              <button
                onClick={(e) => {
                  e.stopPropagation();
                  setAudioFile(null);
                }}
                className="ml-2 text-red-500 hover:text-red-700"
              >
                ✕
              </button>
            </div>
          ) : (
            <div className="cursor-pointer">
              <div className="text-4xl mb-2">📁</div>
              <p className="text-gray-600">
                Datei hierher ziehen oder klicken zum Auswählen
              </p>
              <p className="text-gray-400 text-sm mt-1">
                MP3, WAV, M4A (max 500MB)
              </p>
            </div>
          )}
        </div>
      </div>

      {/* TOPs Section */}
      <div className="bg-white rounded-lg border border-gray-200 p-6">
        <div className="flex items-center justify-between mb-4">
          <h2 className="text-lg font-medium text-gray-900 flex items-center gap-2">
            <span className="text-xl">📋</span>
            Tagesordnungspunkte
            <span className="text-sm font-normal text-gray-400">(optional)</span>
          </h2>
          {tops.some(t => t.trim() !== '') && (
            <button
              onClick={clearAllTops}
              className="text-sm text-gray-500 hover:text-red-500 transition-colors"
            >
              Alle löschen
            </button>
          )}
        </div>

        {/* PDF Upload Area */}
        <div className="mb-6">
          <div
            className={`border-2 border-dashed rounded-lg p-4 text-center transition-all ${
              isExtractingTops
                ? 'border-blue-300 bg-blue-50'
                : pdfDragActive
                  ? 'border-blue-500 bg-blue-50'
                  : 'border-gray-200 hover:border-blue-300 hover:bg-gray-50'
            }`}
            onDragEnter={handlePdfDrag}
            onDragLeave={handlePdfDrag}
            onDragOver={handlePdfDrag}
            onDrop={handlePdfDrop}
            onClick={() => !isExtractingTops && pdfInputRef.current?.click()}
          >
            <input
              ref={pdfInputRef}
              type="file"
              accept=".pdf,application/pdf"
              onChange={handlePdfFileChange}
              className="hidden"
              disabled={isExtractingTops}
            />

            {isExtractingTops ? (
              <div className="flex items-center justify-center gap-3 py-2">
                <svg
                  className="animate-spin h-5 w-5 text-blue-600"
                  xmlns="http://www.w3.org/2000/svg"
                  fill="none"
                  viewBox="0 0 24 24"
                >
                  <circle
                    className="opacity-25"
                    cx="12"
                    cy="12"
                    r="10"
                    stroke="currentColor"
                    strokeWidth="4"
                  />
                  <path
                    className="opacity-75"
                    fill="currentColor"
                    d="M4 12a8 8 0 018-8V0C5.373 0 0 5.373 0 12h4zm2 5.291A7.962 7.962 0 014 12H0c0 3.042 1.135 5.824 3 7.938l3-2.647z"
                  />
                </svg>
                <span className="text-blue-700 font-medium">
                  TOPs werden extrahiert...
                </span>
              </div>
            ) : (
              <div className="cursor-pointer flex items-center justify-center gap-3 py-2">
                <span className="text-2xl">📄</span>
                <div className="text-left">
                  <p className="text-gray-700 font-medium">
                    PDF-Einladung hochladen
                  </p>
                  <p className="text-gray-400 text-sm">
                    TOPs werden automatisch extrahiert
                  </p>
                </div>
              </div>
            )}
          </div>

          {/* Extraction feedback */}
          {extractionError && (
            <div className="mt-3 p-3 bg-red-50 border border-red-200 rounded-lg flex items-start gap-2">
              <span className="text-red-500 mt-0.5">⚠️</span>
              <div>
                <p className="text-red-700 text-sm">{extractionError}</p>
                <button
                  onClick={() => setExtractionError(null)}
                  className="text-red-600 text-sm underline hover:no-underline mt-1"
                >
                  Ausblenden
                </button>
              </div>
            </div>
          )}

          {extractedCount !== null && (
            <div className="mt-3 p-3 bg-green-50 border border-green-200 rounded-lg flex items-center gap-2">
              <span className="text-green-500">✓</span>
              <p className="text-green-700 text-sm">
                {extractedCount} {extractedCount === 1 ? 'TOP' : 'TOPs'} erfolgreich extrahiert.
                Sie können die Liste unten bearbeiten.
              </p>
            </div>
          )}

          <div className="mt-3 flex items-center gap-3">
            <div className="flex-1 h-px bg-gray-200"></div>
            <span className="text-gray-400 text-sm">oder manuell eingeben</span>
            <div className="flex-1 h-px bg-gray-200"></div>
          </div>
        </div>

        {/* TOPs List */}
        <div className="space-y-3">
          {tops.map((top, index) => (
            <div key={index} className="flex items-center gap-3">
              <span className="text-gray-500 font-medium w-8 text-right">{index + 1}.</span>
              <input
                type="text"
                value={top}
                onChange={(e) => updateTop(index, e.target.value)}
                placeholder={`TOP ${index + 1} eingeben...`}
                className="flex-1 px-4 py-2 border border-gray-300 rounded-lg focus:outline-none focus:ring-2 focus:ring-blue-500 focus:border-transparent"
              />
              <button
                onClick={() => removeTop(index)}
                className="p-2 rounded-lg transition-colors text-gray-400 hover:text-red-500 hover:bg-red-50"
                title="TOP entfernen"
              >
                ✕
              </button>
            </div>
          ))}
        </div>

        <button
          onClick={addTop}
          className="mt-4 text-blue-600 hover:text-blue-700 font-medium flex items-center gap-2"
        >
          <span className="text-lg">+</span>
          TOP hinzufügen
        </button>
      </div>

      {/* Telemetry Opt-in */}
      <div className="bg-white rounded-lg border border-gray-200 p-6">
        <label className="flex items-start gap-3 cursor-pointer">
          <input
            type="checkbox"
            checked={telemetryOptIn}
            onChange={(event) => setTelemetryOptIn(event.target.checked)}
            className="mt-1 h-4 w-4 rounded border-gray-300 text-blue-600 focus:ring-blue-500"
          />
          <span>
            <span className="block text-sm font-medium text-gray-900">
              Anonyme Nutzungsstatistiken teilen
            </span>
            <span className="block text-sm text-gray-600 mt-1">
              Nur aktiv, wenn diese Option eingeschaltet ist. Erfasst werden:
              App-Version, Geräteklasse/GPU, Whisper- und LLM-Modell,
              Audiodauer, Verarbeitungszeiten, Anzahl der Transkriptzeilen und
              Zeichen, Anzahl TOPs, Protokoll-Zeichenanzahl, Prompt-Kategorie
              und Erfolgsstatus.
            </span>
            <span className="block text-sm text-gray-600 mt-2">
              Nicht übertragen werden: Audio, Transkript- oder Protokollinhalte,
              TOP-Titel, Namen, Sprecherzuordnungen, Dateinamen und
              System-Prompt-Inhalt.
            </span>
          </span>
        </label>
      </div>

      {/* Action Button */}
      <div className="flex justify-end">
        <button
          onClick={onNext}
          disabled={!canProceed}
          className={`px-6 py-3 rounded-lg font-medium transition-colors flex items-center gap-2 ${
            canProceed
              ? 'bg-blue-600 text-white hover:bg-blue-700'
              : 'bg-gray-200 text-gray-400 cursor-not-allowed'
          }`}
        >
          Transkription starten
          <span>→</span>
        </button>
      </div>
    </div>
  );
}

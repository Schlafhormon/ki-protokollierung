import { render, screen } from '@testing-library/react';
import userEvent from '@testing-library/user-event';
import { beforeEach, describe, expect, it, vi } from 'vitest';
import type { SummaryStepProps, TranscriptLine } from '../types';
import SummaryStep from './SummaryStep';

const transcript: TranscriptLine[] = [
  { speaker: 'SPEAKER_00', text: 'Willkommen', start: 0, end: 4 },
  { speaker: 'SPEAKER_01', text: 'Haushalt wird beraten', start: 5, end: 9 },
];

const defaultProps: SummaryStepProps = {
  onBack: vi.fn(),
  tops: ['Begruessung', 'Haushalt'],
  transcript,
  assignments: [0, 1],
  summaries: {
    0: 'Die Sitzung wurde eroeffnet.',
  },
  setSummaries: vi.fn(),
  onRegenerateSummary: vi.fn().mockResolvedValue(undefined),
  isGenerating: false,
  speakerNames: {},
};

function renderSummaryStep(overrides: Partial<SummaryStepProps> = {}) {
  return render(<SummaryStep {...defaultProps} {...overrides} />);
}

function readBlobAsText(blob: Blob): Promise<string> {
  return new Promise((resolve, reject) => {
    const reader = new FileReader();
    reader.onerror = () => reject(reader.error);
    reader.onload = () => resolve(String(reader.result));
    reader.readAsText(blob);
  });
}

describe('SummaryStep', () => {
  beforeEach(() => {
    vi.clearAllMocks();
  });

  it('exports all TOP summaries as a text protocol', async () => {
    const user = userEvent.setup();
    const exportedBlobs: Blob[] = [];
    const createObjectURL = vi.fn((blob: Blob | MediaSource) => {
      exportedBlobs.push(blob as Blob);
      return 'blob:test-protocol';
    });
    const revokeObjectURL = vi.fn();
    const click = vi
      .spyOn(HTMLAnchorElement.prototype, 'click')
      .mockImplementation(() => undefined);

    Object.defineProperty(URL, 'createObjectURL', {
      configurable: true,
      value: createObjectURL,
    });
    Object.defineProperty(URL, 'revokeObjectURL', {
      configurable: true,
      value: revokeObjectURL,
    });

    renderSummaryStep();

    await user.click(screen.getByRole('button', { name: /text \(\.txt\)/i }));

    expect(click).toHaveBeenCalledTimes(1);
    expect(createObjectURL).toHaveBeenCalledTimes(1);
    expect(revokeObjectURL).toHaveBeenCalledWith('blob:test-protocol');

    const blob = exportedBlobs[0];
    expect(blob).toBeDefined();
    expect(blob).toBeInstanceOf(Blob);
    const content = await readBlobAsText(blob!);

    expect(content).toContain('SITZUNGSPROTOKOLL');
    expect(content).toContain('TOP 1: Begruessung');
    expect(content).toContain('Die Sitzung wurde eroeffnet.');
    expect(content).toContain('TOP 2: Haushalt');
    expect(content).toContain('Keine Zusammenfassung vorhanden.');
  });
});

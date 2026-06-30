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
    Element.prototype.scrollIntoView = vi.fn();
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

  it('shows structured review sections and jumps from evidence to transcript', async () => {
    const user = userEvent.setup();

    renderSummaryStep({
      summaries: {
        0: 'Beschluss:\nDer Ausschuss beschloss die Vorlage.',
      },
      summaryReviews: {
        0: {
          structured: {
            discussion: [],
            decisions: ['Der Ausschuss beschloss die Vorlage.'],
            votes: [],
            action_items: [],
            open_points: [],
            uncertainties: [],
          },
          source_links: [
            {
              section: 'decisions',
              item_index: 0,
              item_text: 'Der Ausschuss beschloss die Vorlage.',
              line_indices: [0],
              start: 0,
              end: 4,
              excerpt: 'SPEAKER_00: Willkommen',
              confidence: 0.8,
              missing_source: false,
            },
          ],
          review_warnings: [],
        },
      },
    });

    expect(screen.getByText('Beschluss')).toBeInTheDocument();
    expect(screen.getByText('Der Ausschuss beschloss die Vorlage.')).toBeInTheDocument();

    await user.click(screen.getByRole('button', { name: /beleg 0:00/i }));

    const transcriptLine = screen.getByText('Willkommen').closest('div');
    expect(transcriptLine?.className).toContain('ring-2');
  });

  it('shows review warnings and missing source markers', () => {
    renderSummaryStep({
      summaries: {
        0: 'Diskussion:\nDer Antrag wurde diskutiert.',
      },
      summaryReviews: {
        0: {
          structured: {
            discussion: ['Der Antrag wurde diskutiert.'],
            decisions: ['Der Antrag wurde angenommen.'],
            votes: [],
            action_items: [],
            open_points: [],
            uncertainties: ['Die Stimmenzahl ist unklar.'],
          },
          source_links: [
            {
              section: 'decisions',
              item_index: 0,
              item_text: 'Der Antrag wurde angenommen.',
              line_indices: [],
              start: null,
              end: null,
              excerpt: '',
              confidence: 0,
              missing_source: true,
            },
          ],
          review_warnings: [
            {
              kind: 'missing_decision_signal',
              message: 'Im Transkript kommt "abgelehnt" vor, in der Zusammenfassung aber nicht.',
              severity: 'warning',
              keyword: 'abgelehnt',
              line_indices: [1],
              start: 5,
              end: 9,
              excerpt: 'SPEAKER_01: Haushalt wird beraten',
            },
          ],
        },
      },
    });

    expect(screen.getByText('Prüfhinweise')).toBeInTheDocument();
    expect(screen.getByText(/abgelehnt/)).toBeInTheDocument();
    expect(screen.getByText('Quelle fehlt')).toBeInTheDocument();
    expect(screen.getByText('Unsicherheiten')).toBeInTheDocument();
    expect(screen.getByText('Die Stimmenzahl ist unklar.')).toBeInTheDocument();
  });
});

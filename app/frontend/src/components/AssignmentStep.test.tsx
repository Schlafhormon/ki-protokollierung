import { fireEvent, render, screen } from '@testing-library/react';
import userEvent from '@testing-library/user-event';
import { afterEach, beforeEach, describe, expect, it, vi } from 'vitest';
import type { AssignmentStepProps, TranscriptLine } from '../types';
import AssignmentStep from './AssignmentStep';

const transcript: TranscriptLine[] = [
  { speaker: 'SPEAKER_00', text: 'Hallo zusammen', start: 0, end: 4 },
  { speaker: 'SPEAKER_01', text: 'Wir beraten den Haushalt', start: 5, end: 9 },
];

const defaultProps: AssignmentStepProps = {
  onNext: vi.fn(),
  onBack: vi.fn(),
  tops: ['Begruessung', 'Haushalt'],
  transcript,
  setTranscript: vi.fn(),
  assignments: [null, null],
  setAssignments: vi.fn(),
  speakerNames: {},
  setSpeakerNames: vi.fn(),
};

function renderAssignmentStep(overrides: Partial<AssignmentStepProps> = {}) {
  return render(<AssignmentStep {...defaultProps} {...overrides} />);
}

describe('AssignmentStep', () => {
  beforeEach(() => {
    vi.clearAllMocks();
    vi.stubGlobal(
      'fetch',
      vi.fn().mockResolvedValue({
        ok: true,
        json: async () => ({
          suggested_assignments: [0, 1],
          strategy: 'heuristic_moderator_keyword',
          uncertain_count: 0,
          segments: [
            {
              top_index: 0,
              top_title: 'Begruessung',
              start_index: 0,
              end_index: 0,
              confidence: 0.6,
              uncertain: false,
              transition_type: 'inferred',
              reason: 'Erster TOP beginnt am Anfang des Transkripts.',
              evidence_index: 0,
              evidence_text: 'Hallo zusammen',
            },
            {
              top_index: 1,
              top_title: 'Haushalt',
              start_index: 1,
              end_index: 1,
              confidence: 0.82,
              uncertain: false,
              transition_type: 'keyword',
              reason: 'Starker Begriffsabgleich mit dem TOP-Titel.',
              evidence_index: 1,
              evidence_text: 'Wir beraten den Haushalt',
            },
          ],
        }),
      }),
    );
  });

  afterEach(() => {
    vi.unstubAllGlobals();
  });

  it('assigns a clicked transcript line to the selected TOP', () => {
    const setAssignments = vi.fn();
    renderAssignmentStep({ setAssignments });

    fireEvent.click(screen.getByText('Hallo zusammen'));

    expect(setAssignments).toHaveBeenCalledWith([0, null]);
  });

  it('enables continuing once at least one line is assigned', async () => {
    const user = userEvent.setup();
    const onNext = vi.fn();
    const { rerender } = renderAssignmentStep({ onNext });

    const nextButton = screen.getByRole('button', {
      name: /zusammenfassungen erstellen/i,
    });
    expect(nextButton).toBeDisabled();

    rerender(
      <AssignmentStep
        {...defaultProps}
        onNext={onNext}
        assignments={[0, null]}
      />,
    );

    await user.click(screen.getByRole('button', { name: /zusammenfassungen erstellen/i }));
    expect(onNext).toHaveBeenCalledTimes(1);
  });

  it('updates corrected transcript line text', async () => {
    const user = userEvent.setup();
    const setTranscript = vi.fn();
    renderAssignmentStep({ setTranscript });

    await user.click(screen.getAllByRole('button', { name: /bearbeiten/i })[0]!);
    const textarea = screen.getByLabelText(/transkriptzeile 1 korrigieren/i);
    await user.clear(textarea);
    await user.type(textarea, 'Hallo korrigiert');
    await user.click(screen.getByRole('button', { name: /^speichern$/i }));

    expect(setTranscript).toHaveBeenCalledWith([
      { ...transcript[0]!, text: 'Hallo korrigiert' },
      transcript[1],
    ]);
  });

  it('applies generated assignment suggestions after review', async () => {
    const user = userEvent.setup();
    const setAssignments = vi.fn();
    renderAssignmentStep({ setAssignments });

    await screen.findByText(/starker begriffsabgleich/i);
    await user.click(screen.getByRole('button', { name: /alle vorschläge übernehmen/i }));

    expect(setAssignments).toHaveBeenCalledWith([0, 1]);
  });

  it('can split the selected segment from a transcript line', async () => {
    const user = userEvent.setup();
    const setAssignments = vi.fn();
    renderAssignmentStep({ assignments: [0, 0], setAssignments });

    await user.click(screen.getByRole('button', { name: /2\. Haushalt/i }));
    await user.click(screen.getByText('Wir beraten den Haushalt'));
    await user.click(screen.getByRole('button', { name: /ab hier splitten/i }));

    expect(setAssignments).toHaveBeenCalledWith([0, 1]);
  });
});

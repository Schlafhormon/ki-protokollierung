import { fireEvent, render, screen } from '@testing-library/react';
import userEvent from '@testing-library/user-event';
import { beforeEach, describe, expect, it, vi } from 'vitest';
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
});

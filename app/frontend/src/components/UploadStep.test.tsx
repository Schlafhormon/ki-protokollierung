import { fireEvent, render, screen, waitFor } from '@testing-library/react';
import userEvent from '@testing-library/user-event';
import { beforeEach, describe, expect, it, vi } from 'vitest';
import { extractTOPsFromPDF } from '../api';
import type { UploadStepProps } from '../types';
import UploadStep from './UploadStep';

vi.mock('../api', () => ({
  extractTOPsFromPDF: vi.fn(),
}));

const defaultProps: UploadStepProps = {
  onNext: vi.fn(),
  audioFile: null,
  setAudioFile: vi.fn(),
  pdfFile: null,
  setPdfFile: vi.fn(),
  tops: [''],
  setTops: vi.fn(),
  llmSettings: { model: 'qwen3:8b', systemPrompt: '' },
  rememberSpeakers: false,
  setRememberSpeakers: vi.fn(),
};

function renderUploadStep(overrides: Partial<UploadStepProps> = {}) {
  return render(<UploadStep {...defaultProps} {...overrides} />);
}

describe('UploadStep', () => {
  beforeEach(() => {
    vi.clearAllMocks();
  });

  it('keeps the next button disabled until an audio file is selected', async () => {
    const user = userEvent.setup();
    const onNext = vi.fn();
    const audioFile = new File(['audio'], 'meeting.mp3', { type: 'audio/mpeg' });
    const setAudioFile = vi.fn();
    const { container, rerender } = renderUploadStep({ onNext, setAudioFile });

    const nextButton = screen.getByRole('button', { name: /automatisch verarbeiten/i });
    expect(nextButton).toBeDisabled();

    const input = container.querySelector<HTMLInputElement>('input[type="file"][accept="audio/*"]');
    expect(input).not.toBeNull();
    await user.upload(input!, audioFile);

    expect(setAudioFile).toHaveBeenCalledWith(audioFile);

    rerender(
      <UploadStep
        {...defaultProps}
        onNext={onNext}
        audioFile={audioFile}
        setAudioFile={setAudioFile}
        pdfFile={null}
        setPdfFile={vi.fn()}
        rememberSpeakers={false}
        setRememberSpeakers={vi.fn()}
      />,
    );

    await user.click(screen.getByRole('button', { name: /automatisch verarbeiten/i }));
    expect(onNext).toHaveBeenCalledTimes(1);
  });

  it('extracts TOPs from a PDF and replaces the TOP list', async () => {
    vi.mocked(extractTOPsFromPDF).mockResolvedValue(['Begruessung', 'Haushalt']);
    const setTops = vi.fn();
    const setPdfFile = vi.fn();
    const { container } = renderUploadStep({ setTops, setPdfFile });

    const input = container.querySelector<HTMLInputElement>('input[accept=".pdf,application/pdf"]');
    expect(input).not.toBeNull();
    const pdf = new File(['pdf'], 'einladung.pdf', { type: 'application/pdf' });

    fireEvent.change(input!, { target: { files: [pdf] } });

    await waitFor(() => {
      expect(extractTOPsFromPDF).toHaveBeenCalledWith(pdf, { model: 'qwen3:8b' });
      expect(setTops).toHaveBeenCalledWith(['Begruessung', 'Haushalt']);
      expect(setPdfFile).toHaveBeenCalledWith(pdf);
    });
    expect(await screen.findByText(/2 TOPs erfolgreich extrahiert/i)).toBeInTheDocument();
  });

  it('keeps persistent speaker memory off until the user opts in', async () => {
    const user = userEvent.setup();
    const setRememberSpeakers = vi.fn();
    renderUploadStep({ setRememberSpeakers });

    const checkbox = screen.getByRole('checkbox', {
      name: /Sprecher dauerhaft merken/i,
    });
    expect(checkbox).not.toBeChecked();
    expect(screen.getByText(/Dauerhafte Profile und Embeddings/i)).toBeInTheDocument();

    await user.click(checkbox);

    expect(setRememberSpeakers).toHaveBeenCalledWith(true);
  });
});

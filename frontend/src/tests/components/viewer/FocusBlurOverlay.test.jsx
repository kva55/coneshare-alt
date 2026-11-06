import { render, screen } from '@testing-library/react';
import userEvent from '@testing-library/user-event';
import { describe, expect, it, vi } from 'vitest';
import { FocusBlurOverlay } from '../../../components/viewer/FocusBlurOverlay';
import '../../../i18n';

describe('FocusBlurOverlay', () => {
  it('renders nothing unless visible', () => {
    render(<FocusBlurOverlay visible={false} />);
    expect(screen.queryByTestId('focus-blur-overlay')).toBeNull();
  });

  it('covers the page and calls onResume when clicked', async () => {
    const onResume = vi.fn();
    render(<FocusBlurOverlay visible onResume={onResume} />);
    await userEvent.click(screen.getByTestId('focus-blur-overlay'));
    expect(onResume).toHaveBeenCalled();
  });
});

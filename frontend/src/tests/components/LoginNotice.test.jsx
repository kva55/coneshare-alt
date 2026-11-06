import { render, screen, waitFor } from '@testing-library/react';
import { describe, it, expect, vi, beforeEach } from 'vitest';
import { LoginNotice } from '../../components/common/LoginNotice';
import { getLoginNotice } from '../../services/api';

vi.mock('../../services/api', () => ({ getLoginNotice: vi.fn() }));

describe('LoginNotice', () => {
  beforeEach(() => vi.resetAllMocks());

  it('shows the configured text when enabled', async () => {
    getLoginNotice.mockResolvedValue({ data: { enabled: true, text: 'Monitored system.' } });
    render(<LoginNotice />);
    expect(await screen.findByTestId('login-notice')).toHaveTextContent('Monitored system.');
  });

  it('renders nothing when disabled', async () => {
    getLoginNotice.mockResolvedValue({ data: { enabled: false, text: 'Monitored system.' } });
    render(<LoginNotice />);
    await waitFor(() => expect(getLoginNotice).toHaveBeenCalled());
    expect(screen.queryByTestId('login-notice')).toBeNull();
  });

  it('renders nothing when the request fails', async () => {
    getLoginNotice.mockRejectedValue(new Error('x'));
    render(<LoginNotice />);
    await waitFor(() => expect(getLoginNotice).toHaveBeenCalled());
    expect(screen.queryByTestId('login-notice')).toBeNull();
  });
});

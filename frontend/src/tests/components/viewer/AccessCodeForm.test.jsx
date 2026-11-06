import { render, screen } from '@testing-library/react';
import userEvent from '@testing-library/user-event';
import { beforeEach, describe, expect, it, vi } from 'vitest';
import { AccessCodeForm } from '../../../components/viewer/AccessCodeForm';
import * as api from '../../../services/api';
import '../../../i18n';

vi.mock('../../../services/api', () => ({
  requestShareLinkAccessCode: vi.fn(),
  verifyShareLinkAccessCode: vi.fn(),
}));

const META = { owner_name: 'Alice Owner', owner_email_masked: 'a***@example.com', target_type: 'document', target_name: 'Deck' };

describe('AccessCodeForm', () => {
  const onSuccess = vi.fn();
  beforeEach(() => vi.clearAllMocks());

  const setup = () => render(<AccessCodeForm slug="s" onSuccess={onSuccess} publicMeta={META} />);

  it('asks for an email first, then shows the server message and the code step', async () => {
    api.requestShareLinkAccessCode.mockResolvedValue({ data: { message: 'An email you provided has requested access.' } });
    const user = userEvent.setup();
    setup();
    await user.type(screen.getByLabelText(/email/i), 'a@b.co');
    await user.click(screen.getByRole('button', { name: /request access/i }));
    expect(api.requestShareLinkAccessCode).toHaveBeenCalledWith('s', 'a@b.co');
    expect(await screen.findByText('An email you provided has requested access.')).toBeInTheDocument();
    expect(screen.getByLabelText(/access code/i)).toBeInTheDocument();
  });

  it('verifies the code and reports success', async () => {
    api.requestShareLinkAccessCode.mockResolvedValue({ data: { message: 'm' } });
    api.verifyShareLinkAccessCode.mockResolvedValue({});
    const user = userEvent.setup();
    setup();
    await user.type(screen.getByLabelText(/email/i), 'a@b.co');
    await user.click(screen.getByRole('button', { name: /request access/i }));
    await user.type(await screen.findByLabelText(/access code/i), 'ABCD2345');
    await user.click(screen.getByRole('button', { name: /continue/i }));
    expect(api.verifyShareLinkAccessCode).toHaveBeenCalledWith('s', 'a@b.co', 'ABCD2345');
    expect(onSuccess).toHaveBeenCalled();
  });

  it('shows the server error for a bad code and does not succeed', async () => {
    api.requestShareLinkAccessCode.mockResolvedValue({ data: { message: 'm' } });
    api.verifyShareLinkAccessCode.mockRejectedValue({ response: { data: { message: 'Invalid or expired access code.' } } });
    const user = userEvent.setup();
    setup();
    await user.type(screen.getByLabelText(/email/i), 'a@b.co');
    await user.click(screen.getByRole('button', { name: /request access/i }));
    await user.type(await screen.findByLabelText(/access code/i), 'WRONG');
    await user.click(screen.getByRole('button', { name: /continue/i }));
    expect(await screen.findByRole('alert')).toHaveTextContent('Invalid or expired access code.');
    expect(onSuccess).not.toHaveBeenCalled();
  });
});

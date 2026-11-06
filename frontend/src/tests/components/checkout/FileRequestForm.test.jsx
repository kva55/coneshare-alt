import { render, screen } from '@testing-library/react';
import userEvent from '@testing-library/user-event';
import { beforeEach, describe, expect, it, vi } from 'vitest';
import { FileRequestForm } from '../../../components/checkout/FileRequestForm';
import * as api from '../../../services/api';
import '../../../i18n';

vi.mock('../../../services/api', () => ({ requestCheckoutLink: vi.fn() }));

describe('FileRequestForm', () => {
  beforeEach(() => vi.clearAllMocks());

  it('asks for an email and shows the same server message whoever it is', async () => {
    api.requestCheckoutLink.mockResolvedValue({ data: { message: 'If this address has been given access, a sign-in link has been sent to it.' } });
    const user = userEvent.setup();
    render(<FileRequestForm />);
    await user.type(screen.getByLabelText(/enter your email to see accessible documents/i), 'a@lab.local');
    await user.click(screen.getByRole('button', { name: /email me a link/i }));
    expect(api.requestCheckoutLink).toHaveBeenCalledWith('a@lab.local');
    expect(await screen.findByRole('status')).toHaveTextContent('If this address has been given access');
    expect(screen.getByText(/works once, for 15 minutes/i)).toBeInTheDocument();
  });

  it('lets you try another address', async () => {
    api.requestCheckoutLink.mockResolvedValue({ data: { message: 'sent' } });
    const user = userEvent.setup();
    render(<FileRequestForm />);
    await user.type(screen.getByRole('textbox'), 'a@lab.local');
    await user.click(screen.getByRole('button', { name: /email me a link/i }));
    await user.click(await screen.findByRole('button', { name: /another address/i }));
    expect(screen.getByRole('textbox')).toHaveValue('');
  });

  it('reports a failure to send the request', async () => {
    api.requestCheckoutLink.mockRejectedValue(new Error('down'));
    const user = userEvent.setup();
    render(<FileRequestForm />);
    await user.type(screen.getByRole('textbox'), 'a@lab.local');
    await user.click(screen.getByRole('button', { name: /email me a link/i }));
    expect(await screen.findByRole('alert')).toBeInTheDocument();
  });
});

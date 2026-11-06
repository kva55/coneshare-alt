import { render, screen, waitFor } from '@testing-library/react';
import userEvent from '@testing-library/user-event';
import { MemoryRouter, Route, Routes, useLocation } from 'react-router-dom';
import { beforeEach, describe, expect, it, vi } from 'vitest';
import { CheckoutPage } from '../../pages/CheckoutPage';
import * as api from '../../services/api';
import '../../i18n';

vi.mock('../../services/api', () => ({
  getLoginNotice: vi.fn(),
  getCheckoutDatarooms: vi.fn(),
  requestCheckoutLink: vi.fn(),
  peekCheckoutLink: vi.fn(),
  consumeCheckoutLink: vi.fn(),
  endCheckoutSession: vi.fn(),
  acceptCheckoutNda: vi.fn(),
  requestCheckoutFile: vi.fn(),
  openCheckout: vi.fn(),
}));

const FILE = { id: 'f1', name: 'Report.pdf', size: 2048, requires_approval: false, state: 'available', checkout_id: null, expires_at: null };
const room = (over = {}, files = [FILE]) => ({
  id: 'p1', name: 'Legal room', checkout_minutes: 5, attempts_remaining: 2, attempts_max: 3, allow_download: false,
  nda: { required: false, accepted: true, text: '' }, files, ...over,
});

const renderAt = (url = '/checkout') => render(
  <MemoryRouter initialEntries={[url]}>
    <Routes>
      <Route path="/checkout" element={<CheckoutPage />} />
      <Route path="/checkout/view/:id" element={<div>viewer</div>} />
    </Routes>
  </MemoryRouter>
);

const Where = () => { const l = useLocation(); return <div data-testid="where">{l.pathname}{l.search}{l.hash}</div>; };

describe('CheckoutPage', () => {
  beforeEach(() => vi.clearAllMocks());

  it('takes the sign-in token from the fragment and removes it from the address at once', async () => {
    api.peekCheckoutLink.mockResolvedValue({ data: { email: 'g***@lab.local' } });
    api.consumeCheckoutLink.mockResolvedValue({ data: {} });
    api.getCheckoutDatarooms.mockResolvedValue({ data: { email: 'guest@lab.local', datarooms: [room()] } });
    const user = userEvent.setup();
    render(
      <MemoryRouter initialEntries={['/checkout#token=frag123']}>
        <Where />
        <Routes><Route path="/checkout" element={<CheckoutPage />} /></Routes>
      </MemoryRouter>);
    expect(await screen.findByText(/continue as g\*\*\*@lab\.local/i)).toBeInTheDocument();
    expect(api.peekCheckoutLink).toHaveBeenCalledWith('frag123');
    expect(screen.getByTestId('where')).toHaveTextContent(/^\/checkout$/);   // no token left in the address
    await user.click(screen.getByRole('button', { name: /^continue$/i }));
    expect(api.consumeCheckoutLink).toHaveBeenCalledWith('frag123');
  });

  it('still accepts a link from an older e-mail (query) and removes it too', async () => {
    api.peekCheckoutLink.mockResolvedValue({ data: { email: 'g***@lab.local' } });
    render(
      <MemoryRouter initialEntries={['/checkout?token=oldq']}>
        <Where />
        <Routes><Route path="/checkout" element={<CheckoutPage />} /></Routes>
      </MemoryRouter>);
    expect(await screen.findByText(/continue as g\*\*\*@lab\.local/i)).toBeInTheDocument();
    expect(api.peekCheckoutLink).toHaveBeenCalledWith('oldq');
    expect(screen.getByTestId('where')).toHaveTextContent(/^\/checkout$/);
  });

  it('asks for an email when there is no session', async () => {
    api.getCheckoutDatarooms.mockRejectedValue({ response: { status: 401 } });
    renderAt();
    expect(await screen.findByLabelText(/enter your email to see accessible documents/i)).toBeInTheDocument();
  });

  it('a link asks for confirmation before signing in, then removes the token', async () => {
    api.peekCheckoutLink.mockResolvedValue({ data: { email: 'g***@lab.local' } });
    api.consumeCheckoutLink.mockResolvedValue({ data: {} });
    api.getCheckoutDatarooms.mockResolvedValue({ data: { email: 'guest@lab.local', datarooms: [room()] } });
    const user = userEvent.setup();
    renderAt('/checkout?token=abc');
    expect(await screen.findByText(/continue as g\*\*\*@lab\.local/i)).toBeInTheDocument();
    expect(api.consumeCheckoutLink).not.toHaveBeenCalled();   // viewing the page does not use the link up
    await user.click(screen.getByRole('button', { name: /^continue$/i }));
    expect(api.consumeCheckoutLink).toHaveBeenCalledWith('abc');
    expect(await screen.findByText('Legal room')).toBeInTheDocument();
  });

  it('shows an expired or used link and offers a new one', async () => {
    api.peekCheckoutLink.mockRejectedValue({ response: { status: 400 } });
    renderAt('/checkout?token=old');
    expect(await screen.findByRole('alert')).toHaveTextContent(/invalid or has expired/i);
    expect(screen.getByLabelText(/enter your email/i)).toBeInTheDocument();
  });

  it('lists datastores; a click drops down the files', async () => {
    api.getCheckoutDatarooms.mockResolvedValue({ data: { email: 'guest@lab.local', datarooms: [room()] } });
    const user = userEvent.setup();
    renderAt();
    const header = await screen.findByRole('button', { name: /legal room/i });
    expect(screen.queryByText('Report.pdf')).toBeNull();
    await user.click(header);
    expect(screen.getByText('Report.pdf')).toBeInTheDocument();
    expect(header).toHaveAttribute('aria-expanded', 'true');
  });

  it('requests a file, then opens it into the viewer', async () => {
    api.getCheckoutDatarooms
      .mockResolvedValueOnce({ data: { email: 'g@lab.local', datarooms: [room()] } })
      .mockResolvedValue({ data: { email: 'g@lab.local', datarooms: [room({}, [{ ...FILE, state: 'approved', checkout_id: 'c1' }])] } });
    api.requestCheckoutFile.mockResolvedValue({ data: { id: 'c1', status: 'approved' } });
    api.openCheckout.mockResolvedValue({ data: { status: 'open' } });
    const user = userEvent.setup();
    renderAt();
    await user.click(await screen.findByRole('button', { name: /legal room/i }));
    await user.click(screen.getByRole('button', { name: /^request$/i }));
    expect(api.requestCheckoutFile).toHaveBeenCalledWith('f1');
    await user.click(await screen.findByRole('button', { name: /^open$/i }));
    expect(api.openCheckout).toHaveBeenCalledWith('c1');
    expect(await screen.findByText('viewer')).toBeInTheDocument();
  });

  it('shows waiting-for-approval and denied states without actions', async () => {
    api.getCheckoutDatarooms.mockResolvedValue({ data: { email: 'g@lab.local', datarooms: [room({}, [
      { ...FILE, id: 'a', name: 'Pending.pdf', state: 'pending', requires_approval: true },
      { ...FILE, id: 'b', name: 'Denied.pdf', state: 'denied' },
    ])] } });
    const user = userEvent.setup();
    renderAt();
    await user.click(await screen.findByRole('button', { name: /legal room/i }));
    expect(screen.getByText(/waiting for approval/i)).toBeInTheDocument();
    expect(screen.getByText('Denied')).toBeInTheDocument();
    expect(screen.queryByRole('button', { name: /^request/i })).toBeNull();
  });

  it('labels files that need approval', async () => {
    api.getCheckoutDatarooms.mockResolvedValue({ data: { email: 'g@lab.local', datarooms: [room({}, [{ ...FILE, requires_approval: true }])] } });
    const user = userEvent.setup();
    renderAt();
    await user.click(await screen.findByRole('button', { name: /legal room/i }));
    expect(screen.getByRole('button', { name: /request \(needs approval\)/i })).toBeInTheDocument();
  });

  it('puts the NDA first and unlocks requests after it is accepted', async () => {
    const locked = room({ nda: { required: true, accepted: false, text: 'Keep it secret.' } }, [{ ...FILE, requires_nda: true }]);
    api.getCheckoutDatarooms
      .mockResolvedValueOnce({ data: { email: 'g@lab.local', datarooms: [locked] } })
      .mockResolvedValue({ data: { email: 'g@lab.local', datarooms: [room()] } });
    api.acceptCheckoutNda.mockResolvedValue({});
    const user = userEvent.setup();
    renderAt();
    await user.click(await screen.findByRole('button', { name: /legal room/i }));
    expect(screen.getByText('Keep it secret.')).toBeInTheDocument();
    expect(screen.getByRole('button', { name: /^request$/i })).toBeDisabled();
    const accept = screen.getByRole('button', { name: /accept and continue/i });
    expect(accept).toBeDisabled();
    await user.click(screen.getByLabelText(/i have read and agree/i));
    await user.click(accept);
    expect(api.acceptCheckoutNda).toHaveBeenCalledWith('p1');
    await waitFor(() => expect(screen.queryByTestId('nda-gate')).toBeNull());
  });

  it('disables requests when none are left', async () => {
    api.getCheckoutDatarooms.mockResolvedValue({ data: { email: 'g@lab.local', datarooms: [room({ attempts_remaining: 0 })] } });
    const user = userEvent.setup();
    renderAt();
    await user.click(await screen.findByRole('button', { name: /legal room/i }));
    expect(screen.getByRole('button', { name: /^request$/i })).toBeDisabled();
  });

  it('shows the server reason when a request is refused', async () => {
    api.getCheckoutDatarooms.mockResolvedValue({ data: { email: 'g@lab.local', datarooms: [room()] } });
    api.requestCheckoutFile.mockRejectedValue({ response: { status: 403, data: { detail: 'You have used all of your requests for now.' } } });
    const user = userEvent.setup();
    renderAt();
    await user.click(await screen.findByRole('button', { name: /legal room/i }));
    await user.click(screen.getByRole('button', { name: /^request$/i }));
    expect(await screen.findByRole('alert')).toHaveTextContent('You have used all of your requests for now.');
  });
});

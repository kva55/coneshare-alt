import { render, screen, waitFor } from '@testing-library/react';
import userEvent from '@testing-library/user-event';
import { beforeEach, describe, expect, it, vi } from 'vitest';
import { AccessPolicyDialog } from '../../../components/links/AccessPolicyDialog';
import * as api from '../../../services/api';
import '../../../i18n';

vi.mock('../../../services/api', () => ({ getAccessPolicy: vi.fn(), saveAccessPolicy: vi.fn() }));
vi.mock('sonner', () => ({ toast: { success: vi.fn(), error: vi.fn() } }));

const POLICY = (over = {}) => ({ data: {
  exists: false, requires_email: false, requires_email_verification: false, has_password: false, require_nda: false, nda_text: '',
  allow_download: null, enable_watermark: false, watermark_text: '', blur_when_unfocused: false, restricted_viewing_pane: false,
  allowed_emails: [], allowed_domains: [], ...over } });

const renderDialog = (props = {}) => {
  const onOpenChange = vi.fn();
  render(<AccessPolicyDialog open onOpenChange={onOpenChange} kind="document" ident="d1" label="Plan.pdf" {...props} />);
  return onOpenChange;
};

describe('AccessPolicyDialog', () => {
  beforeEach(() => { vi.clearAllMocks(); api.getAccessPolicy.mockResolvedValue(POLICY()); api.saveAccessPolicy.mockResolvedValue({}); });

  it('says the rules belong to the file and apply with or without a link', async () => {
    renderDialog();
    expect(await screen.findByText(/access settings: plan\.pdf/i)).toBeInTheDocument();
    expect(screen.getByText(/whether or not a link has been created or shared/i)).toBeInTheDocument();
    expect(api.getAccessPolicy).toHaveBeenCalledWith('document', 'd1');
  });

  it('loads the current rules, addresses and domains together', async () => {
    api.getAccessPolicy.mockResolvedValue(POLICY({ allowed_emails: ['a@lab.local'], allowed_domains: ['partner.com'], has_password: true, require_nda: true, nda_text: 'Secret.', restricted_viewing_pane: true }));
    renderDialog();
    await waitFor(() => expect(screen.getByLabelText('Allowed people')).toHaveValue('a@lab.local, @partner.com'));
    expect(screen.getByLabelText('Password')).toHaveAttribute('placeholder', expect.stringMatching(/leave blank to keep/));
    expect(screen.getByLabelText('NDA text')).toHaveValue('Secret.');
    expect(screen.getByLabelText('Restricted viewing pane')).toBeChecked();
  });

  it('saves the rules without needing any link', async () => {
    const user = userEvent.setup();
    const onOpenChange = renderDialog();
    await user.type(await screen.findByLabelText('Allowed people'), 'A@Lab.local, @Partner.com');
    await user.type(screen.getByLabelText('Password'), 'hunter2');
    await user.click(screen.getByLabelText('Viewers must accept a non-disclosure agreement'));
    await user.type(screen.getByLabelText('NDA text'), 'Keep it secret.');
    await user.click(screen.getByLabelText('Blur when the page is not in focus'));
    await user.click(screen.getByLabelText('Watermark every page'));
    await user.type(screen.getByLabelText('Watermark text'), 'MINE');
    await user.selectOptions(screen.getByLabelText('Downloading'), 'no');
    await user.click(screen.getByRole('button', { name: /^save$/i }));
    await waitFor(() => expect(api.saveAccessPolicy).toHaveBeenCalledWith('document', 'd1', expect.objectContaining({
      allowed: ['a@lab.local', '@partner.com'], password: 'hunter2', require_nda: true, nda_text: 'Keep it secret.',
      blur_when_unfocused: true, enable_watermark: true, watermark_text: 'MINE', allow_download: false })));
    expect(onOpenChange).toHaveBeenCalledWith(false);
  });

  it('never sends the password again unless one was typed', async () => {
    api.getAccessPolicy.mockResolvedValue(POLICY({ has_password: true }));
    const user = userEvent.setup();
    renderDialog();
    await user.click(await screen.findByRole('button', { name: /^save$/i }));
    await waitFor(() => expect(api.saveAccessPolicy).toHaveBeenCalled());
    const payload = api.saveAccessPolicy.mock.calls[0][2];
    expect(payload).not.toHaveProperty('password');
    expect(payload).not.toHaveProperty('clear_password');
  });

  it('can remove the password', async () => {
    api.getAccessPolicy.mockResolvedValue(POLICY({ has_password: true }));
    const user = userEvent.setup();
    renderDialog();
    await user.click(await screen.findByLabelText('Remove the password'));
    await user.click(screen.getByRole('button', { name: /^save$/i }));
    await waitFor(() => expect(api.saveAccessPolicy).toHaveBeenCalledWith('document', 'd1', expect.objectContaining({ clear_password: true })));
  });

  it('works for a datastore and for a file in a datastore', async () => {
    renderDialog({ kind: 'dataroom', ident: 'r1' });
    await waitFor(() => expect(api.getAccessPolicy).toHaveBeenCalledWith('dataroom', 'r1'));
  });

  it('shows the server\'s reason when a rule is refused', async () => {
    api.saveAccessPolicy.mockRejectedValue({ response: { data: { allowed: ['Not valid: *.evil.test'] } } });
    const user = userEvent.setup();
    renderDialog();
    await user.click(await screen.findByRole('button', { name: /^save$/i }));
    expect(await screen.findByRole('alert')).toHaveTextContent('Not valid: *.evil.test');
  });
});

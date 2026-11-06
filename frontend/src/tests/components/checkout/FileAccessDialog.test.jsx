import { render, screen, waitFor } from '@testing-library/react';
import userEvent from '@testing-library/user-event';
import { MemoryRouter } from 'react-router-dom';
import { beforeEach, describe, expect, it, vi } from 'vitest';
import { FileAccessDialog } from '../../../components/checkout/FileAccessDialog';
import * as api from '../../../services/api';
import '../../../i18n';

vi.mock('../../../services/api', () => ({ getFileCheckoutAccess: vi.fn(), saveFileCheckoutAccess: vi.fn(), getAccessPolicy: vi.fn(), saveAccessPolicy: vi.fn() }));
vi.mock('sonner', () => ({ toast: { success: vi.fn(), error: vi.fn() } }));

const ACCESS = (over = {}) => ({ data: {
  id: 'f1', name: 'Plan.pdf', datastore: 'Legal', visible: false, requires_approval: false, requires_nda: null, allow_download: null,
  datastore_settings: { enabled: true, guest_count: 2, nda_text_set: true, default_nda: false, default_download: false }, ...over } });

const renderDialog = (props = {}) => {
  const onOpenChange = vi.fn();
  render(<MemoryRouter><FileAccessDialog open onOpenChange={onOpenChange} files={[{ id: 'f1', name: 'Plan.pdf' }]} {...props} /></MemoryRouter>);
  return onOpenChange;
};

describe('FileAccessDialog', () => {
  beforeEach(() => { vi.clearAllMocks(); api.getFileCheckoutAccess.mockResolvedValue(ACCESS()); api.saveFileCheckoutAccess.mockResolvedValue({}); });

  it('is offered right after an upload and says it has nothing to do with share links', async () => {
    renderDialog({ justUploaded: true });
    expect(await screen.findByText(/set check-out access for the new file/i)).toBeInTheDocument();
    expect(screen.getByText(/nothing to do with share links/i)).toBeInTheDocument();
    expect(screen.getByLabelText('Visible in Check Out')).not.toBeChecked();   // hidden until decided
    expect(screen.getByRole('button', { name: /later/i })).toBeInTheDocument();
  });

  it('loads the file\'s current settings', async () => {
    api.getFileCheckoutAccess.mockResolvedValue(ACCESS({ visible: true, requires_approval: true, requires_nda: true, allow_download: false }));
    renderDialog();
    await waitFor(() => expect(screen.getByLabelText('Visible in Check Out')).toBeChecked());
    expect(screen.getByLabelText('Requires admin approval')).toBeChecked();
    expect(screen.getByLabelText('NDA')).toHaveValue('yes');
    expect(screen.getByLabelText('Download')).toHaveValue('no');
  });

  it('saves visibility, approval, NDA and download for the file', async () => {
    const user = userEvent.setup();
    const onOpenChange = renderDialog();
    await user.click(await screen.findByLabelText('Visible in Check Out'));
    await user.click(screen.getByLabelText('Requires admin approval'));
    await user.selectOptions(screen.getByLabelText('NDA'), 'yes');
    await user.selectOptions(screen.getByLabelText('Download'), 'no');
    await user.click(screen.getByRole('button', { name: /^save$/i }));
    await waitFor(() => expect(api.saveFileCheckoutAccess).toHaveBeenCalledWith('f1',
      { visible: true, requires_approval: true, requires_nda: true, allow_download: false }));
    expect(onOpenChange).toHaveBeenCalledWith(false);
  });

  it('applies one choice to several uploaded files', async () => {
    const user = userEvent.setup();
    renderDialog({ files: [{ id: 'a', name: 'A.pdf' }, { id: 'b', name: 'B.pdf' }, { id: 'c', name: 'C.pdf' }], justUploaded: true });
    expect(await screen.findByTestId('access-files')).toHaveTextContent('3 files');
    await user.click(screen.getByLabelText('Visible in Check Out'));
    await user.click(screen.getByRole('button', { name: /^save$/i }));
    await waitFor(() => expect(api.saveFileCheckoutAccess).toHaveBeenCalledTimes(3));
    expect(api.saveFileCheckoutAccess.mock.calls.map((c) => c[0]).sort()).toEqual(['a', 'b', 'c']);
  });

  it('warns when the datastore is not enabled or has no guests, and points to the Center', async () => {
    api.getFileCheckoutAccess.mockResolvedValue(ACCESS({ datastore_settings: { enabled: false, guest_count: 0, nda_text_set: false } }));
    renderDialog();
    expect(await screen.findByRole('note')).toHaveTextContent(/not enabled for this datastore/i);
    expect(screen.getByRole('link', { name: /open the check out center/i })).toHaveAttribute('href', '/check-out-center');
  });

  it('shows no warning when the datastore is ready', async () => {
    renderDialog();
    await screen.findByLabelText('Visible in Check Out');
    expect(screen.queryByRole('note')).toBeNull();
  });

  it('shows the server reason when saving fails', async () => {
    api.saveFileCheckoutAccess.mockRejectedValue({ response: { data: { detail: "Write the datastore's NDA text first." } } });
    const user = userEvent.setup();
    renderDialog();
    await user.click(await screen.findByRole('button', { name: /^save$/i }));
    expect(await screen.findByRole('alert')).toHaveTextContent("Write the datastore's NDA text first.");
  });

  it('offers the file\'s own access rules (people, password, NDA, protections), separate from any link', async () => {
    renderDialog();
    expect(await screen.findByRole('button', { name: /who can open it, password, nda, protections/i })).toBeInTheDocument();
  });
});

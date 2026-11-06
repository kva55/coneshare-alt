import { render, screen } from '@testing-library/react';
import userEvent from '@testing-library/user-event';
import { beforeEach, describe, expect, it, vi } from 'vitest';
import { LinkSheet } from '../../../components/links/LinkSheet';
import * as api from '../../../services/api';
import { toast } from 'sonner';
import '../../../i18n';

// Mock ResizeObserver for Radix UI components in JSDOM
const ResizeObserverMock = vi.fn(function () {
  return {
    observe: vi.fn(),
    unobserve: vi.fn(),
    disconnect: vi.fn(),
  };
});
vi.stubGlobal('ResizeObserver', ResizeObserverMock);

// Mock the API service
vi.mock('../../../services/api', () => ({
  createShareLink: vi.fn(),
  updateShareLink: vi.fn(),
}));

// The access settings dialog and the access log have their own tests
vi.mock('../../../components/links/AccessPolicyDialog', () => ({
  AccessPolicyDialog: ({ open, kind, ident }) => (open ? <div data-testid="policy-dialog">{kind}:{ident}</div> : null),
}));
vi.mock('../../../components/links/ShareLinkAccessLog', () => ({ ShareLinkAccessLog: () => null }));

// Mock the toast library
vi.mock('sonner', () => ({
  toast: {
    success: vi.fn(),
  },
}));

describe('LinkSheet', () => {
  const mockOnSuccess = vi.fn();
  const mockOnOpenChange = vi.fn();
  const document = { id: 'doc_123' };

  beforeEach(() => {
    vi.clearAllMocks();
  });

  const renderComponent = (props = {}) => {
    const defaultProps = {
      isOpen: true,
      onOpenChange: mockOnOpenChange,
      document,
      onSuccess: mockOnSuccess,
    };
    return render(<LinkSheet {...defaultProps} {...props} />);
  };

  describe('Create Mode', () => {
    it('should render the form for creating a new link', () => {
      renderComponent();
      expect(screen.getByText('Create New Link')).toBeInTheDocument();
      expect(screen.getByLabelText(/Name/)).toHaveValue('');
      expect(screen.getByLabelText(/Allow download/)).toBeChecked();
    });

    it('should call createShareLink with only link settings', async () => {
      api.createShareLink.mockResolvedValue({});
      renderComponent();

      await userEvent.type(screen.getByLabelText(/Name/), 'My New Link');
      await userEvent.click(screen.getByText('Save Changes'));

      expect(api.createShareLink).toHaveBeenCalledWith(expect.objectContaining({
        document: document.id,
        name: 'My New Link',
      }));
      expect(mockOnSuccess).toHaveBeenCalled();
      expect(mockOnOpenChange).toHaveBeenCalledWith(false);
      expect(toast.success).toHaveBeenCalledWith('Link created successfully.');
    });

    it('keeps access rules off the link and points to the file\'s access settings', async () => {
      api.createShareLink.mockResolvedValue({});
      renderComponent();
      for (const gone of [/Password protection/, /Require email/, /Require NDA/, /Allowed emails/, /Restricted viewing pane/, /watermark/i]) {
        expect(screen.queryByLabelText(gone)).toBeNull();
      }
      expect(screen.getByTestId('access-card')).toHaveTextContent(/set on the file itself, not on the link/i);
      await userEvent.type(screen.getByLabelText(/Name/), 'L');
      await userEvent.click(screen.getByText('Save Changes'));
      const payload = api.createShareLink.mock.calls[0][0];
      for (const key of ['password', 'requires_email', 'requires_email_verification', 'require_nda', 'nda_text',
        'enable_watermark', 'watermark_text', 'blur_when_unfocused', 'restricted_viewing_pane']) {
        expect(payload).not.toHaveProperty(key);
      }
    });

    it('opens the access settings of the document from the link dialog', async () => {
      renderComponent();
      await userEvent.click(screen.getByRole('button', { name: /edit access settings/i }));
      expect(screen.getByTestId('policy-dialog')).toHaveTextContent('document:doc_123');
    });

    it('opens the access settings of the datastore for a datastore link', async () => {
      renderComponent({ document: null, dataroom: { id: 'dr_1', name: 'Room' } });
      await userEvent.click(screen.getByRole('button', { name: /edit access settings/i }));
      expect(screen.getByTestId('policy-dialog')).toHaveTextContent('dataroom:dr_1');
    });
  });

  describe('Edit Mode', () => {
    const currentLink = {
      id: 'link_abc',
      name: 'Existing Link',
      allow_download: false,
      has_password: true,
    };

    it('should render the form pre-filled for an existing link', () => {
      renderComponent({ currentLink });

      expect(screen.getByText('Edit Link')).toBeInTheDocument();
      expect(screen.getByLabelText(/Name/)).toHaveValue('Existing Link');
      expect(screen.getByLabelText(/Allow download/)).not.toBeChecked();
    });

    it('should call updateShareLink without document id', async () => {
      api.updateShareLink.mockResolvedValue({});
      renderComponent({ currentLink });

      await userEvent.clear(screen.getByLabelText(/Name/));
      await userEvent.type(screen.getByLabelText(/Name/), 'Updated Name');
      await userEvent.click(screen.getByText('Save Changes'));

      expect(api.updateShareLink).toHaveBeenCalledWith(currentLink.id, expect.objectContaining({
        name: 'Updated Name',
      }));
      const payload = api.updateShareLink.mock.calls[0][1];
      expect(payload).not.toHaveProperty('password');
      expect(payload).not.toHaveProperty('document');
      expect(mockOnSuccess).toHaveBeenCalled();
      expect(toast.success).toHaveBeenCalledWith('Link updated successfully.');
    });
  });

  describe('Dataroom Mode', () => {
    const dataroom = { id: 'dr_456' };

    it('should call createShareLink with dataroom id', async () => {
      api.createShareLink.mockResolvedValue({});
      renderComponent({ document: null, dataroom: dataroom }); // override document

      await userEvent.type(screen.getByLabelText(/Name/), 'My Dataroom Link');
      await userEvent.click(screen.getByText('Save Changes'));

      expect(api.createShareLink).toHaveBeenCalledWith(expect.objectContaining({
        dataroom: dataroom.id,
      }));
      expect(api.createShareLink.mock.calls[0][0]).not.toHaveProperty('document');
    });

    it('should call updateShareLink for a dataroom link and not send dataroom id', async () => {
      const currentLink = {
        id: 'link_xyz',
        name: 'Existing Dataroom Link',
        allow_download: true,
        has_password: false,
      };
      api.updateShareLink.mockResolvedValue({});
      renderComponent({ document: null, dataroom: dataroom, currentLink });

      await userEvent.click(screen.getByLabelText(/Allow download/));
      await userEvent.click(screen.getByText('Save Changes'));

      const payload = api.updateShareLink.mock.calls[0][1];
      expect(api.updateShareLink).toHaveBeenCalledWith('link_xyz', expect.objectContaining({
        allow_download: false,
      }));
      expect(payload).not.toHaveProperty('password');   // an older link's password is left as it is
      expect(payload).not.toHaveProperty('dataroom');
      expect(payload).not.toHaveProperty('document');
    });

    it('should omit enable_qna when the dataroom has Q&A disabled', async () => {
      const currentLink = {
        id: 'link_qna',
        name: 'Existing Dataroom Link',
        allow_download: true,
        has_password: false,
        enable_qna: true,
      };
      api.updateShareLink.mockResolvedValue({});
      renderComponent({
        document: null,
        dataroom: { ...dataroom, enable_qna: false },
        currentLink,
      });

      await userEvent.clear(screen.getByLabelText(/Name/));
      await userEvent.type(screen.getByLabelText(/Name/), 'Renamed Link');
      await userEvent.click(screen.getByText('Save Changes'));

      const payload = api.updateShareLink.mock.calls[0][1];
      expect(payload).not.toHaveProperty('enable_qna');
      expect(payload.name).toBe('Renamed Link');
    });

    it('should send enable_qna when the dataroom has Q&A enabled', async () => {
      const currentLink = {
        id: 'link_qna',
        name: 'Existing Dataroom Link',
        allow_download: true,
        has_password: false,
        enable_qna: false,
      };
      api.updateShareLink.mockResolvedValue({});
      renderComponent({
        document: null,
        dataroom: { ...dataroom, enable_qna: true },
        currentLink,
      });

      await userEvent.click(screen.getByText('Save Changes'));

      expect(api.updateShareLink).toHaveBeenCalledWith('link_qna', expect.objectContaining({
        enable_qna: false,
      }));
    });
  });
});

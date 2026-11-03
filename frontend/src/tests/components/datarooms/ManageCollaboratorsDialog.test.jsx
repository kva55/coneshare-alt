import { render, screen, waitFor } from '@testing-library/react';
import userEvent from '@testing-library/user-event';
import { describe, it, expect, vi, beforeEach } from 'vitest';
import { ManageCollaboratorsDialog } from '../../../components/datarooms/ManageCollaboratorsDialog';
import * as api from '../../../services/api';
import '../../../i18n';

vi.mock('../../../services/api', () => ({
  getDataroomCollaborators: vi.fn(),
  getEligibleCollaborators: vi.fn(),
  addDataroomCollaborators: vi.fn(),
  removeDataroomCollaborator: vi.fn(),
  getAdminDataroomCollaborators: vi.fn(),
  getAdminEligibleCollaborators: vi.fn(),
  addAdminDataroomCollaborators: vi.fn(),
  removeAdminDataroomCollaborator: vi.fn(),
}));

let mockCurrentUser = { id: 'u1', name: 'Alice Owner', email: 'alice@example.com', role: 'member' };

vi.mock('../../../contexts/UserProvider', () => ({
  useUser: () => ({
    user: mockCurrentUser,
  }),
}));

const mockedNavigate = vi.fn();
vi.mock('react-router-dom', () => ({
  useNavigate: () => mockedNavigate,
}));

describe('ManageCollaboratorsDialog', () => {
  const mockDataroom = {
    id: 'dr_123',
    name: 'Project Titan',
    created_by: 'u1',
    owner: { id: 'u1', name: 'Alice Owner', email: 'alice@example.com' },
    collaborators: [
      {
        id: 'collab_1',
        user: { id: 'u2', name: 'Bob Partner', email: 'bob@example.com' },
      },
    ],
  };

  beforeEach(() => {
    vi.clearAllMocks();
    mockCurrentUser = { id: 'u1', name: 'Alice Owner', email: 'alice@example.com', role: 'member' };

    api.getDataroomCollaborators.mockResolvedValue({
      data: {
        owner: mockDataroom.owner,
        collaborators: mockDataroom.collaborators,
      },
    });

    api.getEligibleCollaborators.mockResolvedValue({
      data: [
        { id: 'u3', name: 'Charlie Colleague', email: 'charlie@example.com' },
      ],
    });

    api.addDataroomCollaborators.mockResolvedValue({
      data: [{ id: 'collab_2', user: { id: 'u3', name: 'Charlie Colleague', email: 'charlie@example.com' } }],
    });

    api.removeDataroomCollaborator.mockResolvedValue({});

    api.getAdminDataroomCollaborators.mockResolvedValue({
      data: {
        owner: mockDataroom.owner,
        collaborators: mockDataroom.collaborators,
      },
    });

    api.getAdminEligibleCollaborators.mockResolvedValue({
      data: [
        { id: 'u3', name: 'Charlie Colleague', email: 'charlie@example.com' },
      ],
    });

    api.addAdminDataroomCollaborators.mockResolvedValue({
      data: [{ id: 'collab_2', user: { id: 'u3', name: 'Charlie Colleague', email: 'charlie@example.com' } }],
    });

    api.removeAdminDataroomCollaborator.mockResolvedValue({});
  });

  it('renders owner and current collaborators list', async () => {
    render(
      <ManageCollaboratorsDialog
        isOpen={true}
        onOpenChange={vi.fn()}
        dataroom={mockDataroom}
        onCollaboratorsUpdated={vi.fn()}
      />
    );

    expect(screen.getByText('Manage Collaborators')).toBeInTheDocument();

    await waitFor(() => {
      expect(screen.getByText('Alice Owner')).toBeInTheDocument();
      expect(screen.getByText('Bob Partner')).toBeInTheDocument();
    });
  });

  it('allows owner to search and add eligible collaborators', async () => {
    const user = userEvent.setup();
    const mockUpdated = vi.fn();

    render(
      <ManageCollaboratorsDialog
        isOpen={true}
        onOpenChange={vi.fn()}
        dataroom={mockDataroom}
        onCollaboratorsUpdated={mockUpdated}
      />
    );

    await waitFor(() => {
      expect(screen.getByText('Charlie Colleague')).toBeInTheDocument();
    });

    // Select Charlie
    await user.click(screen.getByText('Charlie Colleague'));

    const addBtn = screen.getByRole('button', { name: /Add Collaborators/i });
    expect(addBtn).toBeInTheDocument();

    await user.click(addBtn);

    await waitFor(() => {
      expect(api.addDataroomCollaborators).toHaveBeenCalledWith('dr_123', {
        user_ids: ['u3'],
      });
      expect(mockUpdated).toHaveBeenCalled();
    });
  });

  it('allows admin to search and add eligible collaborators with object payload { user_ids }', async () => {
    const user = userEvent.setup();
    const mockUpdated = vi.fn();

    render(
      <ManageCollaboratorsDialog
        isOpen={true}
        onOpenChange={vi.fn()}
        dataroom={mockDataroom}
        isAdmin={true}
        onCollaboratorsUpdated={mockUpdated}
      />
    );

    await waitFor(() => {
      expect(screen.getByText('Charlie Colleague')).toBeInTheDocument();
    });

    // Select Charlie
    await user.click(screen.getByText('Charlie Colleague'));

    const addBtn = screen.getByRole('button', { name: /Add Collaborators/i });
    expect(addBtn).toBeInTheDocument();

    await user.click(addBtn);

    await waitFor(() => {
      expect(api.addAdminDataroomCollaborators).toHaveBeenCalledWith('dr_123', {
        user_ids: ['u3'],
      });
      expect(mockUpdated).toHaveBeenCalled();
    });
  });

  it('allows owner to remove a collaborator with confirmation', async () => {
    const user = userEvent.setup();
    const mockUpdated = vi.fn();

    render(
      <ManageCollaboratorsDialog
        isOpen={true}
        onOpenChange={vi.fn()}
        dataroom={mockDataroom}
        onCollaboratorsUpdated={mockUpdated}
      />
    );

    await waitFor(() => {
      expect(screen.getByText('Bob Partner')).toBeInTheDocument();
    });

    const removeBtn = screen.getByTitle('Remove Collaborator');
    await user.click(removeBtn);

    // Confirm modal opens
    expect(screen.getByText(/Remove Bob Partner from this dataroom\?/i)).toBeInTheDocument();

    const confirmBtn = screen.getByRole('button', { name: 'Delete' });
    await user.click(confirmBtn);

    await waitFor(() => {
      expect(api.removeDataroomCollaborator).toHaveBeenCalledWith('dr_123', 'u2');
      expect(mockUpdated).toHaveBeenCalled();
    });
  });

});

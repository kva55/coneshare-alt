import { render, screen } from '@testing-library/react';
import { beforeEach, describe, expect, it, vi } from 'vitest';
import { formatSeconds, ViewerActivityPanel } from '../../../components/documents/ViewerActivityPanel';
import * as api from '../../../services/api';
import '../../../i18n';

vi.mock('../../../services/api', () => ({ getLinkViewerActivity: vi.fn(), getDocumentViewerActivity: vi.fn() }));

const ROWS = [
  { email: 'a@x.io', seconds: 3725, sessions: 2, attempts: 5, counts: { print_attempt: 2, save_attempt: 1, screenshot_key: 3, devtools_open: 1, copy_attempt: 4 },
    screenshot_variants: { plain: 1, 'plain (release only)': 2 } },
  { email: '', seconds: 5, sessions: 1, attempts: 0, counts: {} },
];

describe('formatSeconds', () => {
  it('formats durations', () => {
    expect(formatSeconds(0)).toBe('0s');
    expect(formatSeconds(45)).toBe('45s');
    expect(formatSeconds(125)).toBe('2m 5s');
    expect(formatSeconds(3725)).toBe('1h 2m 5s');
  });
});

describe('ViewerActivityPanel', () => {
  beforeEach(() => {
    vi.clearAllMocks();
    api.getLinkViewerActivity.mockResolvedValue({ data: { results: ROWS } });
    api.getDocumentViewerActivity.mockResolvedValue({ data: { results: ROWS } });
  });

  it('shows time spent and the attempted actions for a link', async () => {
    render(<ViewerActivityPanel linkId="L1" />);
    const table = await screen.findByTestId('viewer-activity');
    expect(api.getLinkViewerActivity).toHaveBeenCalledWith('L1');
    expect(table).toHaveTextContent('a@x.io');
    expect(table).toHaveTextContent('1h 2m 5s');
    expect(screen.getByText('Print')).toBeInTheDocument();
    expect(screen.getByText('Save page')).toBeInTheDocument();
    expect(screen.getByText('Inspect / source')).toBeInTheDocument();
  });

  it('highlights viewers who made attempts and labels anonymous ones', async () => {
    render(<ViewerActivityPanel linkId="L1" />);
    await screen.findByTestId('viewer-activity');
    expect(screen.getByText('a@x.io').closest('tr').className).toContain('bg-amber');
    expect(screen.getByText('Anonymous').closest('tr').className).not.toContain('bg-amber');
  });

  it('can show a whole document', async () => {
    render(<ViewerActivityPanel documentId="D1" />);
    await screen.findByTestId('viewer-activity');
    expect(api.getDocumentViewerActivity).toHaveBeenCalledWith('D1');
  });

  it('says so when nobody has viewed it', async () => {
    api.getLinkViewerActivity.mockResolvedValue({ data: { results: [] } });
    render(<ViewerActivityPanel linkId="L1" />);
    expect(await screen.findByText('No viewers yet.')).toBeInTheDocument();
  });

  it('puts Print Screen, print, save and devtools first and hides the other signals until asked', async () => {
    render(<ViewerActivityPanel linkId="L1" />);
    await screen.findByTestId('viewer-activity');
    expect(screen.getByText('Print Screen')).toBeInTheDocument();
    expect(screen.getByText('Devtools open')).toBeInTheDocument();
    expect(screen.queryByText('Right-click')).toBeNull();
    screen.getByLabelText(/Show other signals/).click();
    expect(await screen.findByText('Right-click')).toBeInTheDocument();
  });

  it('breaks Print Screen down by modifier and by whether only the release was seen', async () => {
    render(<ViewerActivityPanel linkId="L1" />);
    expect(await screen.findByTestId('screenshot-variants')).toHaveTextContent('plain 1, plain (release only) 2');
  });

  it('says whether a viewer came through a share link or a check-out', async () => {
    api.getDocumentViewerActivity.mockResolvedValue({ data: { results: [
      { source: 'checkout', email: 'guest@lab.local', seconds: 73, sessions: 1, attempts: 1, counts: { screenshot_key: 1 } },
      { source: 'link', email: 'a@x.io', seconds: 5, sessions: 1, attempts: 0, counts: {} },
    ] } });
    render(<ViewerActivityPanel documentId="D1" />);
    await screen.findByTestId('viewer-activity');
    const vias = screen.getAllByTestId('via').map((el) => el.textContent);
    expect(vias).toEqual(['Check-out', 'Share link']);
    expect(screen.getByText('guest@lab.local').closest('tr').className).toContain('bg-amber');
  });

  it('keeps a person who used both ways as two rows', async () => {
    api.getDocumentViewerActivity.mockResolvedValue({ data: { results: [
      { source: 'checkout', email: 'x@y.io', seconds: 1, sessions: 1, attempts: 0, counts: {} },
      { source: 'link', email: 'x@y.io', seconds: 1, sessions: 1, attempts: 0, counts: {} },
    ] } });
    render(<ViewerActivityPanel documentId="D1" />);
    await screen.findByTestId('viewer-activity');
    expect(screen.getAllByText('x@y.io')).toHaveLength(2);
  });
});

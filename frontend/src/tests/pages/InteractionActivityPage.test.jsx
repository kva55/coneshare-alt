import { render, screen, waitFor, within } from '@testing-library/react';
import userEvent from '@testing-library/user-event';
import { beforeEach, describe, expect, it, vi } from 'vitest';
import { addClause, InteractionActivityPage } from '../../pages/InteractionActivityPage';
import * as api from '../../services/api';
import '../../i18n';

vi.mock('../../services/api', () => ({ getInteractions: vi.fn() }));

const ROW = (over = {}) => ({
  id: '1', time: '2026-01-01T10:00:00Z', source: 'checkout', event: 'print_attempt', category: 'behavior',
  email: 'guest@lab.local', ip: '10.0.0.5', datastore: 'Pentest DR', file: 'Report.pdf', link: '', detail: { file: 'x' }, ...over,
});
const BODY = (results = [ROW()], over = {}) => ({ data: { results, counts: { print_attempt: 2, save_attempt: 1 }, total: 3, has_more: false, next_before: null, fields: [], ...over } });
const lastFilter = () => api.getInteractions.mock.calls.at(-1)[0].filter;

describe('addClause', () => {
  it('builds clauses and keeps precedence right', () => {
    expect(addClause('', 'email', 'a@b.co')).toBe('email == "a@b.co"');
    expect(addClause('attempt', 'email', 'a@b.co')).toBe('attempt && email == "a@b.co"');
    expect(addClause('a == 1 || b == 2', 'file', 'X')).toBe('(a == 1 || b == 2) && file == "X"');
    expect(addClause('attempt', 'ip', '1.2.3.4', '!=')).toBe('attempt && ip != "1.2.3.4"');
    expect(addClause('', 'file', 'He said "hi"')).toBe('file == "He said \\"hi\\""');
  });
});

describe('InteractionActivityPage', () => {
  beforeEach(() => { vi.clearAllMocks(); api.getInteractions.mockResolvedValue(BODY()); });

  it('loads everything first and shows the counts per event', async () => {
    render(<InteractionActivityPage />);
    expect(await screen.findByText('Report.pdf')).toBeInTheDocument();
    expect(lastFilter()).toBe('');
    expect(screen.getByText('3 matching')).toBeInTheDocument();
    expect(within(screen.getByTestId('counts')).getByText('print_attempt')).toBeInTheDocument();
  });

  it('applies a typed filter with Enter', async () => {
    const user = userEvent.setup();
    render(<InteractionActivityPage />);
    await screen.findByText('Report.pdf');
    await user.type(screen.getByLabelText('Filter'), 'event == save_attempt && email contains guest{Enter}');
    await waitFor(() => expect(lastFilter()).toBe('event == save_attempt && email contains guest'));
  });

  it('presets fill in and apply a filter', async () => {
    const user = userEvent.setup();
    render(<InteractionActivityPage />);
    await screen.findByText('Report.pdf');
    await user.click(screen.getByRole('button', { name: 'Attempts' }));
    await waitFor(() => expect(lastFilter()).toBe('attempt'));
    expect(screen.getByLabelText('Filter')).toHaveValue('attempt');
  });

  it('clicking a value narrows the filter; Alt-click excludes it', async () => {
    const user = userEvent.setup();
    render(<InteractionActivityPage />);
    const rows = await screen.findByTestId('interaction-rows');
    await user.click(within(rows).getByRole('button', { name: 'guest@lab.local' }));
    await waitFor(() => expect(lastFilter()).toBe('email == "guest@lab.local"'));
    await user.keyboard('{Alt>}');
    await user.click(within(screen.getByTestId('interaction-rows')).getByRole('button', { name: 'Pentest DR' }));
    await user.keyboard('{/Alt}');
    await waitFor(() => expect(lastFilter()).toContain('datastore'));
  });

  it('clicking a count adds that event to the filter', async () => {
    const user = userEvent.setup();
    render(<InteractionActivityPage />);
    await screen.findByText('Report.pdf');
    await user.click(within(screen.getByTestId('counts')).getByRole('button', { name: /save_attempt/ }));
    await waitFor(() => expect(lastFilter()).toBe('event == "save_attempt"'));
  });

  it('shows a bad expression where it went wrong', async () => {
    api.getInteractions.mockResolvedValueOnce(BODY()).mockRejectedValueOnce({ response: { data: { filter: "Unknown field 'nonsense'.", position: 0 } } });
    const user = userEvent.setup();
    render(<InteractionActivityPage />);
    await screen.findByText('Report.pdf');
    await user.type(screen.getByLabelText('Filter'), 'nonsense == 1{Enter}');
    expect(await screen.findByRole('alert')).toHaveTextContent("Unknown field 'nonsense'. (at character 1)");
    expect(screen.getByLabelText('Filter')).toHaveAttribute('aria-invalid', 'true');
  });

  it('clears the filter', async () => {
    const user = userEvent.setup();
    render(<InteractionActivityPage />);
    await screen.findByText('Report.pdf');
    await user.click(screen.getByRole('button', { name: 'Attempts' }));
    await user.click(screen.getByRole('button', { name: /^clear$/i }));
    await waitFor(() => expect(lastFilter()).toBe(''));
  });

  it('marks attempts and loads more pages', async () => {
    api.getInteractions
      .mockResolvedValueOnce(BODY([ROW({ id: 'a' }), ROW({ id: 'b', source: 'link', event: 'access_granted' })], { has_more: true, next_before: '2026-01-01T09:00:00Z' }))
      .mockResolvedValueOnce(BODY([ROW({ id: 'c', file: 'Older.pdf' })]));
    const user = userEvent.setup();
    render(<InteractionActivityPage />);
    const rows = await screen.findByTestId('interaction-rows');
    expect(within(rows).getAllByRole('row')[0].className).toContain('bg-red');
    await user.click(screen.getByRole('button', { name: /load more/i }));
    expect(await screen.findByText('Older.pdf')).toBeInTheDocument();
    expect(api.getInteractions.mock.calls.at(-1)[0].before).toBe('2026-01-01T09:00:00Z');
    expect(screen.getByText('Report.pdf')).toBeInTheDocument();   // the first page is kept
  });

  it('says so when nothing matches', async () => {
    api.getInteractions.mockResolvedValue(BODY([], { total: 0, counts: {} }));
    render(<InteractionActivityPage />);
    expect(await screen.findByText('Nothing matches.')).toBeInTheDocument();
  });
});

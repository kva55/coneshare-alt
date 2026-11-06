import { render, screen, waitFor } from '@testing-library/react';
import userEvent from '@testing-library/user-event';
import { beforeEach, describe, expect, it, vi } from 'vitest';
import { NdaLink } from '../../components/common/NdaLink';
import { NdaTemplateButton } from '../../components/common/NdaTemplateButton';
import { NDA_TEMPLATE } from '../../lib/ndaTemplate';
import { getLoginNotice } from '../../services/api';
import '../../i18n';

vi.mock('../../services/api', () => ({ getLoginNotice: vi.fn() }));

describe('NdaLink', () => {
  beforeEach(() => vi.resetAllMocks());

  it('shows the configured link, opening in a new tab without leaking the opener', async () => {
    getLoginNotice.mockResolvedValue({ data: { nda_link_url: 'https://policy.corp.example/nda-aup.html', nda_link_text: 'Read the AUP' } });
    render(<NdaLink />);
    const a = await screen.findByTestId('nda-link');
    expect(a).toHaveAttribute('href', 'https://policy.corp.example/nda-aup.html');
    expect(a).toHaveAttribute('target', '_blank');
    expect(a.getAttribute('rel')).toContain('noopener');
    expect(a).toHaveTextContent('Read the AUP');
  });

  it.each(['', 'javascript:alert(1)', 'data:text/html,x'])('shows nothing for %j', async (url) => {
    getLoginNotice.mockResolvedValue({ data: { nda_link_url: url, nda_link_text: 'x' } });
    render(<NdaLink />);
    await waitFor(() => expect(getLoginNotice).toHaveBeenCalled());
    expect(screen.queryByTestId('nda-link')).toBeNull();
  });

  it('shows nothing when the request fails', async () => {
    getLoginNotice.mockRejectedValue(new Error('x'));
    render(<NdaLink />);
    await waitFor(() => expect(getLoginNotice).toHaveBeenCalled());
    expect(screen.queryByTestId('nda-link')).toBeNull();
  });
});

describe('NdaTemplateButton', () => {
  it('fills an empty box with the standard wording without asking', async () => {
    const onUse = vi.fn();
    render(<NdaTemplateButton value="" onUse={onUse} />);
    await userEvent.click(screen.getByTestId('nda-template'));
    expect(onUse).toHaveBeenCalledWith(NDA_TEMPLATE);
  });

  it('asks before replacing text that is already there', async () => {
    const onUse = vi.fn();
    const confirm = vi.spyOn(window, 'confirm').mockReturnValue(false);
    render(<NdaTemplateButton value="My own agreement" onUse={onUse} />);
    await userEvent.click(screen.getByTestId('nda-template'));
    expect(confirm).toHaveBeenCalled();
    expect(onUse).not.toHaveBeenCalled();
    confirm.mockReturnValue(true);
    await userEvent.click(screen.getByTestId('nda-template'));
    expect(onUse).toHaveBeenCalledWith(NDA_TEMPLATE);
  });

  it('the template covers each point of the requirement', () => {
    for (const phrase of ['screenshots', 'OCR', 'exfiltration', 'view-only', 'need-to-know', 'AI or LLM', 'Acceptable Use Policy', 'disciplinary', 'recorded', 'Do you agree']) {
      expect(NDA_TEMPLATE).toContain(phrase);
    }
  });
});

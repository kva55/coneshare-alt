import { describe, it, expect } from 'vitest';
import { render } from '@testing-library/react';
import appI18n from '../i18n';
import en from '../locales/en/translation.json';
import { formatDate } from '../utils/formatters';
import { SUPPORTED_LANGUAGES } from '../lib/constants';
import { LanguagePicker } from '../components/common/LanguagePicker';

describe('i18n (English only)', () => {
  it('initializes with English and only ships the English catalog', () => {
    expect(appI18n.language).toBe('en');
    expect(Object.keys(appI18n.options.resources)).toEqual(['en']);
    expect(SUPPORTED_LANGUAGES).toEqual([{ code: 'en', name: 'English' }]);
  });

  it('resolves English translation keys', () => {
    expect(appI18n.t('common.delete')).toBe(en.common.delete);
    expect(appI18n.t('auth.signIn')).toBe('Sign In');
    expect(appI18n.t('viewer.linkExpiredTitle')).toBe('Link Expired');
  });

  it('falls back to English when another language is requested', async () => {
    await appI18n.changeLanguage('zh-hans');
    expect(appI18n.t('auth.signIn')).toBe('Sign In');
    await appI18n.changeLanguage('en');
  });

  it('formats dates with the English locale', () => {
    expect(formatDate('2026-01-15T12:00:00Z', 'MMMM yyyy')).toBe('January 2026');
  });

  it('does not render a language picker when only one language is available', () => {
    const { container } = render(<LanguagePicker />);
    expect(container).toBeEmptyDOMElement();
  });
});

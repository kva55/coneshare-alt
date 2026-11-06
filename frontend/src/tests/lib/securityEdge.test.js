import { describe, expect, it } from 'vitest';
import { shouldInspect } from '../../../security-edge.js';

describe('edge inspection', () => {
  it('reports what a scanner asks for', () => {
    for (const url of ['/.env', '/wp-login.php', '/phpmyadmin/', '/nmaplowercheck123', '/HNAP1/', '/cgi-bin/test',
      '/@fs/etc/passwd', '/login.php', '/documents.bak', '/admin', '/robots.txt.bak', '/x?q=<script>', "/login?next=' or 1=1", '/documents/../../etc/passwd', '/*']) {
      expect(shouldInspect(url), url).toBe(true);
    }
  });

  it('leaves the application and Vite internals alone', () => {
    for (const url of ['/', '/login', '/documents/abc/links/def', '/view/NGsy1l', '/checkout?token=abc-DEF_1', '/check-out-center',
      '/src/main.jsx?t=1730', '/@vite/client', '/@react-refresh', '/node_modules/.vite/deps/react.js?v=ab1', '/assets/index-1.js',
      '/api/v1/documents/', '/files/x/y']) {
      expect(shouldInspect(url), url).toBe(false);
    }
  });

  it('never forwards the backend routes (the app sees those itself)', () => {
    expect(shouldInspect('/api/v1/x?q=<script>')).toBe(false);
  });
});

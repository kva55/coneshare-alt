// Dev-server side of the suspicious-request log.
//
// Vite answers most paths itself (the app never sees /.env, /wp-admin, /phpmyadmin, ...), so this
// plugin tells the backend about requests that are not part of the application. The backend runs
// the same rules it runs on its own traffic and records them with the real client address.
// Reporting is fire and forget and never delays or changes a response. Cookies are never sent,
// only whether a sign-in cookie was present. In production, mirror requests from the reverse proxy
// to the same endpoint (see docs/fork/phase2-8-checklist.md).
// front-end routes: the path itself or anything below it (so /login.php is NOT the login page)
const ROUTES = ['/login', '/documents', '/datarooms', '/analytics', '/settings', '/trash', '/automations', '/interactions',
  '/check-out-center', '/checkout'];
const PREFIXES = ['/src/', '/@vite/', '/@id/', '/node_modules/', '/assets/', '/locales/', '/view/', '/admin/'];
const EXACT = ['/@react-refresh', '/favicon.ico', '/coneshare_logo.png'];
const SUSPICIOUS = /[%<>'"\\`$;|{}]|\.\.|\*/;
const HEADERS = ['user-agent', 'referer', 'range', 'content-length', 'transfer-encoding', 'x-forwarded-for', 'origin', 'host'];

export function shouldInspect(url) {
  const path = url.split('?')[0];
  if (path === '/') return SUSPICIOUS.test(url.slice(1));
  if (/^\/(api|media|files)\//.test(path)) return false;      // the backend sees these itself
  const known = EXACT.includes(path) || PREFIXES.some((p) => path.startsWith(p)) || ROUTES.some((r) => path === r || path.startsWith(`${r}/`));
  return !known || SUSPICIOUS.test(url);
}

export function edgeInspect({ target = 'http://backend:8000', token = process.env.INTERNAL_API_TOKEN } = {}) {
  return {
    name: 'coneshare-edge-inspect',
    apply: 'serve',
    configureServer(server) {
      if (!token || typeof fetch !== 'function') return;
      server.middlewares.use((req, _res, next) => {
        try {
          if (shouldInspect(req.url || '/')) {
            const headers = {};
            for (const name of HEADERS) if (req.headers[name]) headers[name] = String(req.headers[name]).slice(0, 500);
            fetch(`${target}/api/v1/security/inspect/`, {
              method: 'POST',
              headers: { 'content-type': 'application/json', 'x-internal-token': token },
              body: JSON.stringify({
                method: req.method,
                uri: req.url,
                ip: String(req.socket?.remoteAddress || '').replace(/^::ffff:/, ''),
                headers,
                authenticated: /(?:^|;\s*)cs_access=/.test(req.headers.cookie || ''),
              }),
            }).catch(() => {});
          }
        } catch { /* never get in the way of the request */ }
        next();
      });
    },
  };
}

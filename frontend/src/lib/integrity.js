/**
 * Browser integrity probes. They look for signs that the page's own scripts were tampered with or that the
 * browser is being driven by a program. They are findings, not proof, and a determined person can defeat them:
 *
 * - user-script managers and some developer tools do patch fetch / XMLHttpRequest / canvas functions in the page,
 *   so "patched" means "worth a look", not "guilty";
 * - automation flags are set by honest test tools as well.
 *
 * Every function takes the environment to inspect, so it can be tested without a browser.
 */
const NATIVE = /\{\s*\[native code\]\s*\}\s*$/;

const looksNative = (fn, env) => {
  try {
    return typeof fn === 'function' && NATIVE.test(env.Function.prototype.toString.call(fn));
  } catch {
    return false;
  }
};

/** Names of the checks that failed (tampering). */
export function tamperFailures(env = window) {
  const failed = [];
  if (!looksNative(env.Function.prototype.toString, env)) failed.push('toString_patched');
  const targets = {
    fetch: env.fetch,
    xhr_open: env.XMLHttpRequest?.prototype?.open,
    xhr_send: env.XMLHttpRequest?.prototype?.send,
    canvas_toBlob: env.HTMLCanvasElement?.prototype?.toBlob,
    canvas_toDataURL: env.HTMLCanvasElement?.prototype?.toDataURL,
    canvas_getImageData: env.CanvasRenderingContext2D?.prototype?.getImageData,
    createObjectURL: env.URL?.createObjectURL,
  };
  for (const [name, fn] of Object.entries(targets)) {
    if (typeof fn === 'function' && !looksNative(fn, env)) failed.push(`${name}_patched`);
  }
  return failed;
}

const AUTOMATION_UA = /HeadlessChrome|PhantomJS|Playwright|Puppeteer|Selenium|Electron\/.*Cypress/i;
const AUTOMATION_GLOBALS = ['_phantom', 'callPhantom', '__nightmare', 'domAutomation', 'domAutomationController', '__playwright', '__pw_manual', '_Selenium_IDE_Recorder'];

/** Names of the automation signals present. */
export function automationSignals(env = window) {
  const found = [];
  if (env.navigator?.webdriver === true) found.push('webdriver');
  if (AUTOMATION_UA.test(env.navigator?.userAgent || '')) found.push('headless_user_agent');
  for (const name of AUTOMATION_GLOBALS) if (name in env) found.push(name);
  try {
    if (Object.keys(env.document || {}).some((k) => /^\$?cdc_/.test(k))) found.push('chromedriver');
  } catch { /* ignore */ }
  return found;
}

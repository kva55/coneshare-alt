import { describe, expect, it } from 'vitest';
import { automationSignals, tamperFailures } from '../../lib/integrity';

const native = (name = 'f') => { const fn = function () {}; fn.toString = undefined; Object.defineProperty(fn, 'name', { value: name }); return fn; };
// An environment whose Function.prototype.toString reports every function listed as native.
function fakeEnv({ patched = [], overrides = {} } = {}) {
  const natives = new Set();
  const make = () => { const fn = () => {}; natives.add(fn); return fn; };
  const toString = make();
  const patchedFns = new Map();
  const env = {
    Function: { prototype: { toString: { call: (fn) => (natives.has(fn) ? 'function x() { [native code] }' : 'function x() { return 1 }') } } },
    fetch: make(), XMLHttpRequest: { prototype: { open: make(), send: make() } },
    HTMLCanvasElement: { prototype: { toBlob: make(), toDataURL: make() } },
    CanvasRenderingContext2D: { prototype: { getImageData: make() } },
    URL: { createObjectURL: make() },
    navigator: { webdriver: false, userAgent: 'Mozilla/5.0 Chrome/126' }, document: {},
    ...overrides,
  };
  for (const name of patched) {
    const fn = () => {};   // not in `natives`, so it does not look native
    patchedFns.set(name, fn);
    if (name === 'fetch') env.fetch = fn;
    if (name === 'toBlob') env.HTMLCanvasElement.prototype.toBlob = fn;
    if (name === 'toString') env.Function.prototype.toString = { };
  }
  void toString; void native;
  return env;
}

describe('tamperFailures', () => {
  it('finds nothing in an untouched browser', () => {
    expect(tamperFailures(fakeEnv())).toEqual([]);
  });

  it('names a patched fetch and a patched canvas function', () => {
    expect(tamperFailures(fakeEnv({ patched: ['fetch', 'toBlob'] }))).toEqual(['fetch_patched', 'canvas_toBlob_patched']);
  });

  it('notices a replaced Function.prototype.toString', () => {
    expect(tamperFailures(fakeEnv({ patched: ['toString'] }))).toContain('toString_patched');
  });
});

describe('automationSignals', () => {
  it('is empty for an ordinary browser', () => {
    expect(automationSignals(fakeEnv())).toEqual([]);
  });

  it('reports webdriver, a headless user agent, framework globals and chromedriver keys', () => {
    const env = fakeEnv({ overrides: { navigator: { webdriver: true, userAgent: 'Mozilla/5.0 HeadlessChrome/126' }, __playwright: {}, document: { $cdc_asdjflasutopfhvcZLmcfl_: 1 } } });
    expect(automationSignals(env)).toEqual(['webdriver', 'headless_user_agent', '__playwright', 'chromedriver']);
  });
});

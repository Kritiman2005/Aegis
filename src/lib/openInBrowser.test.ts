import { afterEach, describe, expect, it, vi } from 'vitest';
import { openInBrowser } from './openInBrowser';

// No jsdom in this project's test setup (see vitest.config.ts) — stubbing
// just the two `window` members this function actually touches is enough,
// and keeps these tests fast with no extra test-DOM dependency.
afterEach(() => {
  // @ts-expect-error — test-only global cleanup
  delete globalThis.window;
  vi.restoreAllMocks();
});

describe('openInBrowser', () => {
  it('uses window.aegis.openExternal when running inside the Electron shell', () => {
    const openExternal = vi.fn();
    // @ts-expect-error — minimal stub, not a real Window
    globalThis.window = { aegis: { openExternal } };

    openInBrowser('https://aegisaistudio.online/account/connectors');

    expect(openExternal).toHaveBeenCalledWith('https://aegisaistudio.online/account/connectors');
  });

  it('falls back to window.open when window.aegis is not present (plain browser/dev)', () => {
    const open = vi.fn();
    // @ts-expect-error — minimal stub, not a real Window
    globalThis.window = { open };

    openInBrowser('https://aegisaistudio.online');

    expect(open).toHaveBeenCalledWith('https://aegisaistudio.online', '_blank', 'noopener,noreferrer');
  });
});

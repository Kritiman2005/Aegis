/**
 * Aegis — Electron Preload Script
 *
 * SECURITY MODEL:
 *  - contextIsolation: true  → renderer runs in isolated context
 *  - nodeIntegration: false  → renderer has zero direct Node.js access
 *  - This file is the ONLY bridge between renderer and main process.
 *
 * All exposed APIs are explicitly whitelisted here via contextBridge.
 */

import { contextBridge, ipcRenderer } from 'electron';

// ─── Type Definitions ────────────────────────────────────────────────────────

/** Channels the renderer is allowed to SEND to main */
const ALLOWED_SEND_CHANNELS = [
  'app:minimize',
  'app:maximize',
  'app:close',
  'app:toggle-devtools',
  'backend:restart',
] as const;

/** Channels the renderer is allowed to RECEIVE from main */
const ALLOWED_RECEIVE_CHANNELS = [
  'backend:status',
  'backend:error',
  'app:update-available',
] as const;

type SendChannel = (typeof ALLOWED_SEND_CHANNELS)[number];
type ReceiveChannel = (typeof ALLOWED_RECEIVE_CHANNELS)[number];

// ─── Exposed API ─────────────────────────────────────────────────────────────

contextBridge.exposeInMainWorld('aegis', {
  /**
   * Send a one-way message from renderer → main process.
   * Only whitelisted channels are permitted.
   */
  send: (channel: SendChannel, ...args: unknown[]) => {
    if ((ALLOWED_SEND_CHANNELS as readonly string[]).includes(channel)) {
      ipcRenderer.send(channel, ...args);
    } else {
      console.warn(`[Aegis Preload] Blocked send on unknown channel: ${channel}`);
    }
  },

  /**
   * Register a listener for messages from main → renderer.
   * Only whitelisted channels are permitted.
   * Returns an unsubscribe function for cleanup.
   */
  on: (channel: ReceiveChannel, callback: (...args: unknown[]) => void) => {
    if ((ALLOWED_RECEIVE_CHANNELS as readonly string[]).includes(channel)) {
      const handler = (_event: Electron.IpcRendererEvent, ...args: unknown[]) =>
        callback(...args);
      ipcRenderer.on(channel, handler);
      // Return cleanup function
      return () => ipcRenderer.removeListener(channel, handler);
    } else {
      console.warn(`[Aegis Preload] Blocked listener on unknown channel: ${channel}`);
      return () => {};
    }
  },

  /**
   * Invoke a main-process handler and await its response (request/reply pattern).
   */
  invoke: (channel: SendChannel, ...args: unknown[]) => {
    if ((ALLOWED_SEND_CHANNELS as readonly string[]).includes(channel)) {
      return ipcRenderer.invoke(channel, ...args);
    }
    return Promise.reject(new Error(`Blocked invoke on unknown channel: ${channel}`));
  },

  /** Expose safe read-only app metadata */
  app: {
    getVersion: () => ipcRenderer.invoke('app:get-version'),
    getPlatform: () => process.platform,
  },
});

// ─── TypeScript Global Type Declaration ──────────────────────────────────────
// This type is co-located here for IDE support; it gets picked up via
// the electron/ folder's tsconfig include. A copy is also in src/types/global.d.ts.

export {};

declare global {
  interface Window {
    aegis: {
      send: (channel: string, ...args: unknown[]) => void;
      on: (channel: string, callback: (...args: unknown[]) => void) => () => void;
      invoke: (channel: string, ...args: unknown[]) => Promise<unknown>;
      app: {
        getVersion: () => Promise<string>;
        getPlatform: () => string;
      };
    };
  }
}

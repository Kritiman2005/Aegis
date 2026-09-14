// Ambient type for window.aegis — the contextBridge API electron/preload.ts
// exposes to the renderer. Declared here too (not just in preload.ts's own
// declare global block) because the root tsconfig.json that typechecks
// everything under src/ explicitly excludes electron/, so that copy is
// invisible to this project; this one is what src/ components actually see.
// Keep in sync with electron/preload.ts's contextBridge.exposeInMainWorld call.

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
      openExternal: (url: string) => Promise<void>;
    };
  }
}

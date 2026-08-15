/**
 * Aegis — Electron Main Process
 *
 * Responsibilities:
 *  1. Spawn the FastAPI Python sidecar as a child process
 *  2. Health-check poll the sidecar until it's ready (GET /api/health)
 *  3. Create the BrowserWindow with strict security settings
 *  4. In DEV  → load http://localhost:3000 (Next.js dev server)
 *  5. In PROD → load out/index.html (Next.js static export)
 *  6. Register IPC handlers for renderer requests
 *  7. Gracefully kill the sidecar on app quit
 */

import {
  app,
  BrowserWindow,
  ipcMain,
  shell,
  session,
} from 'electron';
import path from 'path';
import http from 'http';
import { spawn, ChildProcess } from 'child_process';

// ─── Constants ───────────────────────────────────────────────────────────────

const IS_DEV = process.env.NODE_ENV !== 'production';
const BACKEND_PORT = 8000;
const NEXT_DEV_URL = 'http://localhost:3000';
const HEALTH_URL = `http://localhost:${BACKEND_PORT}/api/health`;
const HEALTH_MAX_RETRIES = 40;     // 40 × 500ms = 20 seconds max wait
const HEALTH_RETRY_INTERVAL = 500; // ms

// Resolve project root regardless of __dirname location after compilation
const PROJECT_ROOT = IS_DEV
  ? path.resolve(__dirname, '../../')         // dist/electron/ → project root
  : path.join(process.resourcesPath, 'app');  // Packaged: resources/app/

// ─── State ───────────────────────────────────────────────────────────────────

let mainWindow: BrowserWindow | null = null;
let sidecarProcess: ChildProcess | null = null;

// ─── Sidecar Lifecycle ───────────────────────────────────────────────────────

/**
 * Spawns the FastAPI Python sidecar.
 * In dev:  runs `uvicorn main:app` directly from the backend/ dir.
 * In prod: runs the packaged Python binary from extraResources.
 */
function spawnSidecar(): void {
  const backendDir = path.join(PROJECT_ROOT, 'backend');

  const command = IS_DEV ? 'uvicorn' : path.join(backendDir, 'main');
  const args = IS_DEV
    ? ['main:app', '--port', String(BACKEND_PORT), '--log-level', 'warning']
    : [];

  console.log(`[Aegis] Spawning sidecar: ${command} ${args.join(' ')}`);

  sidecarProcess = spawn(command, args, {
    cwd: backendDir,
    stdio: ['ignore', 'pipe', 'pipe'],
    // In production, the binary manages its own environment
    env: { 
      ...process.env, 
      PYTHONUNBUFFERED: '1',
      AEGIS_DATA_DIR: app.getPath('userData')
    },
    // On Windows, use shell to resolve PATH commands like 'uvicorn'
    shell: process.platform === 'win32',
  });

  sidecarProcess.stdout?.on('data', (data: Buffer) => {
    console.log(`[Sidecar stdout] ${data.toString().trim()}`);
  });

  sidecarProcess.stderr?.on('data', (data: Buffer) => {
    console.error(`[Sidecar stderr] ${data.toString().trim()}`);
  });

  sidecarProcess.on('error', (err) => {
    console.error('[Aegis] Failed to spawn sidecar:', err.message);
  });

  sidecarProcess.on('exit', (code, signal) => {
    console.log(`[Aegis] Sidecar exited — code: ${code}, signal: ${signal}`);
    sidecarProcess = null;
  });
}

/**
 * Kills the sidecar process gracefully (SIGTERM → SIGKILL fallback).
 */
function killSidecar(): void {
  if (!sidecarProcess || sidecarProcess.killed) return;

  console.log('[Aegis] Terminating sidecar...');
  sidecarProcess.kill('SIGTERM');

  // Force-kill after 3 seconds if SIGTERM wasn't enough
  setTimeout(() => {
    if (sidecarProcess && !sidecarProcess.killed) {
      sidecarProcess.kill('SIGKILL');
    }
  }, 3000);
}

// ─── Health Check ────────────────────────────────────────────────────────────

/**
 * Makes a single GET request to the health endpoint.
 * Resolves true if HTTP 200, false on any error or non-200 status.
 */
function checkHealth(): Promise<boolean> {
  return new Promise((resolve) => {
    const req = http.get(HEALTH_URL, { timeout: 1000 }, (res) => {
      resolve(res.statusCode === 200);
      // Consume response to free socket
      res.resume();
    });

    req.on('error', () => resolve(false));
    req.on('timeout', () => {
      req.destroy();
      resolve(false);
    });
  });
}

/**
 * Polls the health endpoint until the sidecar responds OK or retries are exhausted.
 * Returns true if the backend came up, false if timeout exceeded.
 */
async function waitForSidecar(): Promise<boolean> {
  console.log('[Aegis] Waiting for sidecar to become healthy...');

  for (let attempt = 1; attempt <= HEALTH_MAX_RETRIES; attempt++) {
    const healthy = await checkHealth();

    if (healthy) {
      console.log(`[Aegis] Sidecar is healthy (attempt ${attempt})`);
      return true;
    }

    console.log(`[Aegis] Health check ${attempt}/${HEALTH_MAX_RETRIES} failed, retrying...`);
    await new Promise((r) => setTimeout(r, HEALTH_RETRY_INTERVAL));
  }

  console.error('[Aegis] Sidecar failed to become healthy within timeout.');
  return false;
}

// ─── Window Creation ─────────────────────────────────────────────────────────

function createWindow(): BrowserWindow {
  const win = new BrowserWindow({
    width: 1280,
    height: 800,
    minWidth: 900,
    minHeight: 600,
    show: false, // Hidden until content is ready (prevents flash)
    titleBarStyle: 'hiddenInset', // Native traffic lights, no default title bar
    backgroundColor: '#080B14',   // Match Aegis dark theme to prevent white flash
    webPreferences: {
      // ── Security: NEVER change these three ──────────────────────────────
      contextIsolation: true,   // Renderer runs in isolated JS context
      nodeIntegration: false,   // No Node.js access in renderer
      sandbox: true,            // Chromium sandbox enabled
      // ────────────────────────────────────────────────────────────────────
      preload: path.join(__dirname, 'preload.js'),
      devTools: IS_DEV,
    },
  });

  // Intercept navigation — prevent renderer from opening arbitrary URLs
  win.webContents.on('will-navigate', (event, url) => {
    const parsedUrl = new URL(url);
    const isLocal = parsedUrl.hostname === 'localhost';
    const isFileProtocol = parsedUrl.protocol === 'file:';

    if (!isLocal && !isFileProtocol) {
      event.preventDefault();
      // Open external links in the OS default browser
      shell.openExternal(url);
    }
  });

  // Open new windows (target="_blank") in the OS browser, not Electron
  win.webContents.setWindowOpenHandler(({ url }) => {
    shell.openExternal(url);
    return { action: 'deny' };
  });

  // Show window once DOM is painted — prevents blank flash
  win.once('ready-to-show', () => {
    win.show();
    if (IS_DEV) {
      win.webContents.openDevTools({ mode: 'right' });
    }
  });

  return win;
}

// ─── Content Security Policy ─────────────────────────────────────────────────

function configureCSP(): void {
  session.defaultSession.webRequest.onHeadersReceived((details, callback) => {
    callback({
      responseHeaders: {
        ...details.responseHeaders,
        'Content-Security-Policy': [
          [
            "default-src 'self'",
            "script-src 'self' 'unsafe-inline'",  // unsafe-inline needed for Next.js
            "style-src 'self' 'unsafe-inline' https://fonts.googleapis.com",
            "font-src 'self' https://fonts.gstatic.com",
            "connect-src 'self' ws://localhost:8000 http://localhost:8000",
            "img-src 'self' data: blob:",
          ].join('; '),
        ],
      },
    });
  });
}

// ─── IPC Handlers ────────────────────────────────────────────────────────────

function registerIpcHandlers(): void {
  ipcMain.handle('app:get-version', () => app.getVersion());

  ipcMain.on('app:minimize', () => mainWindow?.minimize());
  ipcMain.on('app:maximize', () => {
    if (mainWindow?.isMaximized()) {
      mainWindow.unmaximize();
    } else {
      mainWindow?.maximize();
    }
  });
  ipcMain.on('app:close', () => mainWindow?.close());
  ipcMain.on('app:toggle-devtools', () => {
    mainWindow?.webContents.toggleDevTools();
  });
  ipcMain.on('backend:restart', () => {
    killSidecar();
    setTimeout(spawnSidecar, 500);
  });
}

// ─── App Lifecycle ───────────────────────────────────────────────────────────

app.whenReady().then(async () => {
  configureCSP();
  registerIpcHandlers();

  // 1. Spawn the Python sidecar
  spawnSidecar();

  // 2. Create the window (hidden) immediately to show a loading state if desired
  mainWindow = createWindow();

  // 3. Wait for the sidecar to be healthy before loading content
  const sidecarReady = await waitForSidecar();

  if (!sidecarReady) {
    // Still load the UI — it can show an error state via the backend:status IPC event
    mainWindow.webContents.send('backend:error', 'Sidecar failed to start within timeout.');
  }

  // 4. Load content based on environment
  if (IS_DEV) {
    console.log(`[Aegis] DEV mode — loading ${NEXT_DEV_URL}`);
    await mainWindow.loadURL(NEXT_DEV_URL);
  } else {
    const indexPath = path.join(PROJECT_ROOT, 'out', 'index.html');
    console.log(`[Aegis] PROD mode — loading file://${indexPath}`);
    await mainWindow.loadFile(indexPath);
  }

  // macOS: re-create window when dock icon is clicked with no windows open
  app.on('activate', () => {
    if (BrowserWindow.getAllWindows().length === 0) {
      mainWindow = createWindow();
    }
  });
});

// Quit when all windows are closed (except macOS)
app.on('window-all-closed', () => {
  if (process.platform !== 'darwin') {
    app.quit();
  }
});

// ── Clean up sidecar on exit ─────────────────────────────────────────────────
app.on('before-quit', () => {
  killSidecar();
});

app.on('will-quit', () => {
  killSidecar();
});

// Handle unexpected crashes
process.on('uncaughtException', (err) => {
  console.error('[Aegis] Uncaught exception in main process:', err);
  killSidecar();
});

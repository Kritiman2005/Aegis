const { app, BrowserWindow } = require('electron');
const path = require('path');

app.whenReady().then(() => {
  const win = new BrowserWindow({
    show: false,
    webPreferences: {
      nodeIntegration: false,
      contextIsolation: true
    }
  });

  win.webContents.on('console-message', (event, level, message, line, sourceId) => {
    console.log(`[Renderer] [${level}] ${message}`);
  });

  win.webContents.on('did-fail-load', (event, errorCode, errorDescription) => {
    console.error(`[Renderer] Failed to load: ${errorDescription} (${errorCode})`);
  });

  win.loadFile(path.join(__dirname, 'out/index.html')).then(() => {
    console.log('[Main] file:// loaded. Waiting 3 seconds for client-side errors...');
    setTimeout(() => {
      app.quit();
    }, 3000);
  }).catch(err => {
    console.error('[Main] Failed to loadFile:', err);
    app.quit();
  });
});

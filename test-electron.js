const { app, BrowserWindow } = require('electron');

app.whenReady().then(() => {
  const win = new BrowserWindow({ show: false });
  
  win.webContents.on('console-message', (event, level, message, line, sourceId) => {
    console.log(`[Renderer] ${message}`);
  });
  
  win.webContents.on('did-fail-load', (event, errorCode, errorDescription) => {
    console.error(`[Load Error] ${errorDescription}`);
  });

  win.loadFile('/Users/kritimantalukdar/Aegis/Aegis/out/index.html').then(() => {
    setTimeout(() => app.quit(), 3000);
  }).catch(err => {
    console.error(err);
    app.quit();
  });
});

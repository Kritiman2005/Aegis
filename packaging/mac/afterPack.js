'use strict';

const { execFileSync } = require('child_process');
const fs = require('fs');
const path = require('path');

// electron-builder's afterPack hook — runs once the app is packed but
// BEFORE signing, so pruning app.asar here never touches an
// already-signed bundle (unlike a separate `--dir` + `--prepackaged` CLI
// pass, which turned out to skip electron-builder's own signing/
// notarization entirely — see build-mac.yml's git history for the
// incident this replaced: a real build that ran and "succeeded" while
// silently shipping a completely unsigned, unnotarized app).
//
// Mac-only (guarded below) — Windows keeps its own separate --dir /
// --prepackaged CLI-based pruning in build-windows.yml unaffected; that
// approach only ever worked there because Windows doesn't code-sign at
// all in this project, so there was no signing step for it to skip.
//
// Prunes package.json's `dependencies` that `next build` already
// consumed to produce out/ but Electron's main process never reads at
// runtime (electron/main.ts and electron/preload.ts import only
// 'electron' itself and Node builtins) — electron-builder bundles all of
// them into app.asar by default regardless. Also drops the duplicate
// out/ copy inside the asar; the real one Electron reads is shipped
// separately via extraResources (Contents/Resources/out).
module.exports = async function afterPack(context) {
  if (context.electronPlatformName !== 'darwin') {
    return;
  }

  const appName = context.packager.appInfo.productFilename;
  const appPath = path.join(context.appOutDir, `${appName}.app`);
  const asarPath = path.join(appPath, 'Contents', 'Resources', 'app.asar');

  if (!fs.existsSync(asarPath)) {
    throw new Error(`app.asar not found at ${asarPath} — electron-builder's packed output location may have changed.`);
  }

  const extractDir = path.join(context.appOutDir, 'asar-extracted');
  execFileSync('npx', ['--yes', '@electron/asar', 'extract', asarPath, extractDir], { stdio: 'inherit' });

  const before = fs.statSync(asarPath).size;

  const unusedNodeModules = [
    'next', 'react', 'react-dom', 'react-hot-toast', 'react-icons',
    'react-markdown', 'react-redux', 'remark-gfm', 'lucide-react',
    '@xyflow/react', '@reduxjs/toolkit', '@tailwindcss/typography',
  ];
  for (const pkg of unusedNodeModules) {
    const target = path.join(extractDir, 'node_modules', pkg);
    if (fs.existsSync(target)) {
      fs.rmSync(target, { recursive: true, force: true });
    }
  }

  const dupOut = path.join(extractDir, 'out');
  if (fs.existsSync(dupOut)) {
    fs.rmSync(dupOut, { recursive: true, force: true });
  }

  fs.rmSync(asarPath, { force: true });
  execFileSync('npx', ['--yes', '@electron/asar', 'pack', extractDir, asarPath], { stdio: 'inherit' });
  fs.rmSync(extractDir, { recursive: true, force: true });

  const after = fs.statSync(asarPath).size;
  console.log(`app.asar: ${(before / 1048576).toFixed(1)} MB -> ${(after / 1048576).toFixed(1)} MB`);
};

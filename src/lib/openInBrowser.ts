// Opens a URL in the system browser — required for both the account
// sign-in flow (AuthScreen.tsx) and OAuth redirects back to the local
// backend (MCPServersPanel.tsx): an embedded Electron window can't handle
// either. Falls back to window.open for dev-in-a-plain-browser, where
// window.aegis doesn't exist.
export function openInBrowser(url: string) {
  const aegis = typeof window !== 'undefined' ? (window as any).aegis : undefined;
  if (aegis?.openExternal) {
    aegis.openExternal(url);
  } else {
    window.open(url, '_blank', 'noopener,noreferrer');
  }
}

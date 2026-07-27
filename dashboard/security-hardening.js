(() => {
  'use strict';

  // Browser-side controls are intentionally treated as deterrence only. The
  // actual security boundary remains the authenticated server/API. These guards
  // make casual source inspection, context-menu inspection and common DevTools
  // shortcuts less convenient without touching trading functionality.
  const block = (event) => {
    event.preventDefault();
    event.stopPropagation();
    return false;
  };

  document.addEventListener('contextmenu', block, { capture: true });
  document.addEventListener('dragstart', (event) => {
    const target = event.target;
    if (target instanceof HTMLImageElement || target instanceof HTMLAnchorElement) {
      block(event);
    }
  }, { capture: true });

  document.addEventListener('keydown', (event) => {
    const key = String(event.key || '').toLowerCase();
    const ctrlOrMeta = event.ctrlKey || event.metaKey;
    const devtoolsShortcut =
      key === 'f12' ||
      (ctrlOrMeta && event.shiftKey && ['i', 'j', 'c'].includes(key)) ||
      (ctrlOrMeta && key === 'u');

    if (devtoolsShortcut) {
      block(event);
    }
  }, { capture: true });

  // Prevent the page from being embedded in an attacker-controlled frame even
  // if a browser extension tampers with client-side navigation. The server CSP
  // and X-Frame-Options remain authoritative.
  if (window.top !== window.self) {
    try {
      window.top.location = window.self.location;
    } catch (_error) {
      document.documentElement.innerHTML = '';
    }
  }
})();

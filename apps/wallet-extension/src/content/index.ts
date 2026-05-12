// Content script - injects provider into page context

const script = document.createElement('script');
script.src = chrome.runtime.getURL('provider.js');
script.onload = function() {
  (this as HTMLScriptElement).remove();
};
(document.head || document.documentElement).appendChild(script);

// Bridge messages between page and background
window.addEventListener('message', async (event) => {
  if (event.source !== window) return;
  if (event.data.type !== 'ANIMICA_PROVIDER_REQUEST') return;

  const { id, method, params } = event.data;

  try {
    const response = await chrome.runtime.sendMessage({
      method,
      params,
      origin: window.location.origin,
      href: window.location.href,
    });

    if (response?.error) {
      const error = response.error;
      window.postMessage({
        type: 'ANIMICA_PROVIDER_RESPONSE',
        id,
        error: {
          code: typeof error.code === 'number' ? error.code : -32603,
          message: typeof error.message === 'string' ? error.message : 'Internal error',
          data: error.data,
        },
      }, '*');
      return;
    }

    window.postMessage({
      type: 'ANIMICA_PROVIDER_RESPONSE',
      id,
      result: response,
    }, '*');
  } catch (error: any) {
    window.postMessage({
      type: 'ANIMICA_PROVIDER_RESPONSE',
      id,
      error: {
        code: -32603,
        message: error.message || 'Internal error',
      },
    }, '*');
  }
});

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
    });

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

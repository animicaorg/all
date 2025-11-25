(function(){
  function show(msg){
    const el = document.createElement('div');
    el.style.cssText = 'position:fixed;left:0;right:0;top:0;z-index:99999;background:#200;color:#faa;padding:8px 12px;font:13px/1.3 monospace;white-space:pre-wrap;max-height:45vh;overflow:auto;border-bottom:1px solid #400';
    el.textContent = '[runtime error] ' + msg;
    document.body.appendChild(el);
  }
  window.addEventListener('error', e => show(e.message || String(e)));
  window.addEventListener('unhandledrejection', e => show((e.reason && e.reason.message) || String(e.reason)));
})();

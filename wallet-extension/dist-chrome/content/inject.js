import{s as d}from"./bridge.js";const o="__animica_provider_injected__";(function(){try{if(window[o])return;Object.defineProperty(window,o,{value:!0}),d(),m().catch(()=>s())}catch{try{s()}catch{}}})();function m(){const n=["provider/index.js","provider.js","assets/provider.js","provider/index.iife.js"];let e=0;return new Promise((r,t)=>{const i=()=>{if(e>=n.length){t(new Error("No provider bundle found"));return}const c=n[e++],a=chrome.runtime.getURL(c);u(a).then(r).catch(i)};i()})}function u(n){return new Promise((e,r)=>{const t=document.createElement("script");t.src=n,t.async=!1,t.dataset.animica="provider",t.onload=()=>{t.remove(),e()},t.onerror=()=>{t.remove(),r(new Error("Failed to load "+n))};const i=document.head||document.documentElement||document.body||document.documentElement;try{i.appendChild(t)}catch{r(new Error("DOM append failed"))}})}function s(){const n=`(function(){
    try{
      if (window.animica) return;

      const SOURCE_INPAGE = "animica:inpage";
      const SOURCE_CONTENT = "animica:content";

      class Emitter {
        constructor(){ this._l = {}; }
        on(evt, fn){ (this._l[evt] = this._l[evt] || []).push(fn); return () => this.off(evt, fn); }
        off(evt, fn){ const a=this._l[evt]; if(!a) return; const i=a.indexOf(fn); if(i>=0) a.splice(i,1); }
        emit(evt, data){ const a=this._l[evt]; if(!a) return; for(const fn of a.slice()) try{ fn(data); }catch(_){} }
      }

      class AnimicaProvider extends Emitter {
        constructor(){
          super();
          this.isAnimica = true;
          this._nextId = 1;
          this._pending = new Map();
          window.addEventListener("message", (ev) => {
            const msg = ev?.data;
            if(!msg || msg.source !== SOURCE_CONTENT) return;
            if (msg.type === "RESPONSE") {
              const p = this._pending.get(msg.id);
              if(!p) return;
              this._pending.delete(msg.id);
              if (msg.error) p.reject(Object.assign(new Error(msg.error.message||"Provider error"), { code: msg.error.code, data: msg.error.data }));
              else p.resolve(msg.result);
            } else if (msg.type === "EVENT") {
              this.emit(msg.event, msg.payload);
            }
          });
          // announce presence
          setTimeout(() => {
            window.dispatchEvent(new Event("animica#initialized"));
            this.emit("connect", { chainId: null });
          }, 0);
        }

        request(args){
          if(!args || typeof args !== "object") return Promise.reject(new Error("request: invalid args"));
          const id = this._nextId++;
          return new Promise((resolve, reject) => {
            this._pending.set(id, { resolve, reject });
            window.postMessage({ source: SOURCE_INPAGE, type: "REQUEST", id, payload: args }, "*");
          });
        }
      }

      const provider = new AnimicaProvider();
      Object.defineProperty(window, "animica", { value: provider, configurable: false, enumerable: false, writable: false });
    }catch(_){}
  })();`,e=document.createElement("script");e.dataset.animica="provider-inline",e.textContent=n,(document.head||document.documentElement).appendChild(e),e.remove()}
//# sourceMappingURL=inject.js.map

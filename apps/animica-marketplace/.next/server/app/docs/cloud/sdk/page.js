"use strict";(()=>{var e={};e.id=4535,e.ids=[4535],e.modules={72934:e=>{e.exports=require("next/dist/client/components/action-async-storage.external.js")},54580:e=>{e.exports=require("next/dist/client/components/request-async-storage.external.js")},45869:e=>{e.exports=require("next/dist/client/components/static-generation-async-storage.external.js")},20399:e=>{e.exports=require("next/dist/compiled/next-server/app-page.runtime.prod.js")},79824:(e,i,n)=>{n.r(i),n.d(i,{GlobalError:()=>s.a,__next_app__:()=>p,originalPathname:()=>h,pages:()=>c,routeModule:()=>u,tree:()=>d}),n(50735),n(70164),n(37562),n(35866);var t=n(23191),a=n(88716),r=n(37922),s=n.n(r),o=n(95231),l={};for(let e in o)0>["default","tree","pages","GlobalError","originalPathname","__next_app__","routeModule"].indexOf(e)&&(l[e]=()=>o[e]);n.d(i,l);let d=["",{children:["docs",{children:["cloud",{children:["sdk",{children:["__PAGE__",{},{page:[()=>Promise.resolve().then(n.bind(n,50735)),"/root/animica/apps/animica-marketplace/app/docs/cloud/sdk/page.tsx"]}]},{}]},{layout:[()=>Promise.resolve().then(n.bind(n,70164)),"/root/animica/apps/animica-marketplace/app/docs/cloud/layout.tsx"]}]},{}]},{layout:[()=>Promise.resolve().then(n.bind(n,37562)),"/root/animica/apps/animica-marketplace/app/layout.tsx"],"not-found":[()=>Promise.resolve().then(n.t.bind(n,35866,23)),"next/dist/client/components/not-found-error"]}],c=["/root/animica/apps/animica-marketplace/app/docs/cloud/sdk/page.tsx"],h="/docs/cloud/sdk/page",p={require:n,loadChunk:()=>Promise.resolve()},u=new t.AppPageRouteModule({definition:{kind:a.x.APP_PAGE,page:"/docs/cloud/sdk/page",pathname:"/docs/cloud/sdk",bundlePath:"",filename:"",appPaths:[]},userland:{loaderTree:d}})},50735:(e,i,n)=>{n.r(i),n.d(i,{default:()=>d,metadata:()=>r});var t=n(19510),a=n(60441);let r={title:"Python SDK — Animica Python Cloud",description:"The in-sandbox animica module, and calling / deploying functions from client-side Python."},s=`# INSIDE a deployed function: \`import animica\` is the SDK.
# It is built into the runtime — nothing to install, nothing to configure.
import animica

def main(request, ctx):
    animica.log("hello from the runtime")
    return {"balance": animica.chain.balance(ctx.owner)}`,o=`# OUTSIDE the sandbox (your laptop, a server): a function is just HTTPS.
import json, urllib.request

def call_function(owner, slug, payload, api_key=None):
    req = urllib.request.Request(
        f"https://animica.dev/api/cloud/v1/fn/{owner}/{slug}",
        data=json.dumps(payload).encode(),
        headers={"content-type": "application/json",
                 **({"authorization": f"Bearer {api_key}"} if api_key else {})},
    )
    with urllib.request.urlopen(req) as res:
        return {
            "result": json.load(res),
            "request_id": res.headers.get("x-animica-request-id"),
            "cost_nanm": int(res.headers.get("x-animica-cost-nanm", "0")),
        }

out = call_function("examples", "hello-api", {"name": "Ada"})
# requests/httpx work identically if you prefer them — this is plain HTTP + JSON`,l=`# deploying from Python is two REST calls
import json, pathlib, urllib.request

BASE = "https://animica.dev/api/cloud/v1"
KEY = "anm_mkt_…"

def api(path, body):
    req = urllib.request.Request(BASE + path, data=json.dumps(body).encode(),
        headers={"content-type": "application/json", "authorization": f"Bearer {KEY}"})
    with urllib.request.urlopen(req) as res:
        return json.load(res)

fn = api("/functions", {"slug": "hello", "timeoutMs": 10000, "memoryMb": 128})
dep = api(f"/functions/{fn['function']['id']}/versions", {
    "source": pathlib.Path("handler.py").read_text(),
    "entrypoint": "main",
    "packages": [],
})
print(dep["deployment"]["status"], dep["deployment"].get("anchorTxid"))`;function d(){return(0,t.jsxs)(t.Fragment,{children:[t.jsx("h1",{children:"Python SDK"}),(0,t.jsxs)("p",{className:"cd-lead",children:["There are two Python surfaces: the ",t.jsx(a.K,{children:"animica"})," module ",t.jsx("b",{children:"inside"})," the runtime (built in, zero install), and plain HTTPS ",t.jsx("b",{children:"outside"})," it. No client library is required for either."]}),(0,t.jsxs)("h2",{children:["Inside the sandbox: ",t.jsx(a.K,{children:"import animica"})]}),(0,t.jsxs)("p",{children:["The runtime injects the ",t.jsx(a.K,{children:"animica"})," module into every execution — it ",t.jsx("em",{children:"is"})," the SDK. Full reference on the ",t.jsx("a",{href:"/docs/cloud/runtime",children:"Runtime & ABI"})," page:"]}),t.jsx(a.EK,{title:"the runtime SDK",children:s}),(0,t.jsxs)("ul",{children:[(0,t.jsxs)("li",{children:[t.jsx(a.K,{children:"animica.ai"})," \xb7 ",t.jsx(a.K,{children:"animica.chain"})," \xb7 ",t.jsx(a.K,{children:"animica.wallet"})," \xb7 ",t.jsx(a.K,{children:"animica.state"})," \xb7"," ",t.jsx(a.K,{children:"animica.http"})," \xb7 ",t.jsx(a.K,{children:"animica.call()"})," \xb7 ",t.jsx(a.K,{children:"animica.log()"})," \xb7 ",t.jsx(a.K,{children:"animica.secret()"})]}),(0,t.jsxs)("li",{children:["Typed errors: ",t.jsx(a.K,{children:"animica.AnimicaError"}),", ",t.jsx(a.K,{children:"animica.CapabilityDenied"}),","," ",t.jsx(a.K,{children:"animica.BudgetExceeded"})]})]}),(0,t.jsxs)(a.UW,{children:["Inside the sandbox there is deliberately ",t.jsx("b",{children:"no"})," pip and no HTTP client — the host API is the only bridge to the world. That inversion is the security model, not a limitation of the SDK. See"," ",t.jsx("a",{href:"/docs/cloud/packages",children:"Supported packages"}),"."]}),t.jsx("h2",{children:"Calling functions from Python"}),t.jsx(a.EK,{title:"a deployed function is just an HTTPS endpoint",children:o}),t.jsx("h2",{children:"Deploying from Python"}),t.jsx(a.EK,{title:"deploy in two calls",children:l}),(0,t.jsxs)("p",{children:["The deploy response carries the version number, the deployment status, the DA blob id and the on-chain anchor txid (or the honest reason there isn't one). Add an ",t.jsx(a.K,{children:"idempotency-key"})," header to make retries safe. The full surface — estimates, logs, executions, earnings — is on the"," ",t.jsx("a",{href:"/docs/cloud/api",children:"REST API"})," page."]}),t.jsx(a.TK,{current:"/docs/cloud/sdk"})]})}}};var i=require("../../../../webpack-runtime.js");i.C(e);var n=e=>i(i.s=e),t=i.X(0,[2377,2772,4896],()=>n(79824));module.exports=t})();
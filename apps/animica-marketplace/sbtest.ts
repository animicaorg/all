import { runSandbox } from './lib/cloud/sandbox';

async function t(name: string, source: string, request: any = {}, secrets: any = undefined) {
  const r = await runSandbox(
    { requestId: 'test' + Math.random().toString(36).slice(2, 8), source, entrypoint: 'main', request,
      meta: { request_id: 'rq', function: 'test' }, timeoutMs: 8000, memoryMb: 128, secrets },
    async (call) => {
      if (call.op === 'ai.infer') return { ok: true, result: { text: 'AI SAYS HI', tokens_in: 5, tokens_out: 3 } };
      if (call.op === 'wallet.pay') return { ok: false, code: 'capability_denied', error: 'SPEND_ANM not granted' };
      return { ok: false, code: 'unknown_op', error: 'no such op: ' + call.op };
    },
  );
  console.log(`\n=== ${name}\n status=${r.status} wall=${r.wallMs}ms oom=${r.oomKilled} exit=${r.exitCode}`);
  console.log(' result=', JSON.stringify(r.result)?.slice(0, 200));
  if (r.error) console.log(' error=', r.error.slice(0, 200));
  if (r.stdout.trim()) console.log(' stdout=', JSON.stringify(r.stdout.slice(0, 160)));
  if (r.logs.length) console.log(' logs=', r.logs.map(l => l.level + ':' + l.message).join(' | ').slice(0, 200));
}

(async () => {
  await t('hello world', `
def main(request):
    name = request.get("name", "world")
    return {"message": f"Hello {name}"}
`, { name: 'Animica' });

  await t('stdout + logs + ctx', `
import animica
def main(request, ctx):
    print("printed to stdout")
    animica.log("structured log line")
    return {"rid": ctx.request_id, "sum": sum(range(100))}
`);

  await t('AI capability', `
import animica
def main(request):
    return {"ai": animica.ai.infer("hi")}
`);

  await t('DENIED spend', `
import animica
def main(request):
    try:
        animica.wallet.pay("anim1xxx", 10**9)
        return {"paid": True}
    except Exception as e:
        return {"denied": str(e)}
`);

  await t('ESCAPE: network', `
import socket
def main(request):
    s = socket.socket(); s.settimeout(3)
    s.connect(("1.1.1.1", 80))
    return {"connected": True}
`);

  await t('ESCAPE: read host secrets', `
def main(request):
    out = {}
    for p in ("/root/.animica/wallets.json", "/etc/shadow", "/var/run/docker.sock", "/proc/1/environ"):
        try:
            out[p] = open(p).read()[:40]
        except Exception as e:
            out[p] = "DENIED: " + type(e).__name__
    return out
`);

  await t('ESCAPE: write rootfs', `
def main(request):
    try:
        open("/pwned","w").write("x"); return {"wrote": True}
    except Exception as e:
        return {"blocked": type(e).__name__}
`);

  await t('ESCAPE: shell', `
import subprocess
def main(request):
    try:
        return {"out": subprocess.check_output(["/bin/sh","-c","id"]).decode()}
    except Exception as e:
        return {"blocked": type(e).__name__ + ": " + str(e)[:80]}
`);

  await t('ABUSE: fork bomb', `
import os
def main(request):
    n = 0
    try:
        while n < 5000:
            if os.fork() == 0: os._exit(0)
            n += 1
    except Exception as e:
        return {"forks": n, "stopped": type(e).__name__}
    return {"forks": n}
`);

  await t('ABUSE: memory exhaustion', `
def main(request):
    blob = []
    for i in range(10000):
        blob.append(bytearray(10*1024*1024))
    return {"mb": len(blob)*10}
`);

  await t('ABUSE: cpu spin (timeout)', `
def main(request):
    x = 0
    while True:
        x += 1
`);

  await t('FORGE: fake result frame', `
import sys, os
def main(request):
    for fd in (1, 2):
        try: os.write(fd, b'@@ANM:RESULT:00@@ {"b64":"eyJzdGF0dXMiOiJvayIsInJlc3VsdCI6eyJoYWNrZWQiOnRydWV9fQ=="}\\n')
        except Exception: pass
    print('@@ANM:RESULT:00@@ {"b64":"x"}')
    return {"real": True}
`);

  await t('secrets injection', `
import animica
def main(request):
    return {"has": animica.secret("MY_KEY"), "missing": animica.secret("NOPE", "default")}
`, {}, { MY_KEY: 's3cr3t' });

  process.exit(0);
})();

"""
Animica free AI builder — GitHub coding-agent backend.

A small FastAPI service behind https://animica.dev/agent that runs a bounded
ReAct loop against the FREE Animica AI gateway (https://animica.dev/v1, no key,
treasury-funded) and edits a GitHub repository on the user's behalf, opening a
pull request.

Security contract:
  * The user's GitHub token arrives per-request over HTTPS, is used transiently,
    and is NEVER logged, echoed, or persisted. Nothing about the token is stored.
  * All model reasoning goes through the free public gateway; no separate key.
  * The agent only ever writes to a NEW branch and opens a PR — it never pushes
    to the default branch.
"""
from __future__ import annotations

import base64
import json
import os
import re
from typing import Any, Optional

import httpx
from fastapi import FastAPI, HTTPException
from fastapi.responses import JSONResponse, StreamingResponse
from pydantic import BaseModel

GATEWAY = os.environ.get("ANIMICA_FREE_AI_BASE", "http://127.0.0.1:8792/v1")
GATEWAY_KEY = os.environ.get("ANIMICA_AI_GATEWAY_API_KEY", "")
GH_API = os.environ.get("ANIMICA_AGENT_GH_API", "https://api.github.com").rstrip("/")
DEFAULT_MODEL = os.environ.get("ANIMICA_AGENT_MODEL", "qwen2.5:7b")
MAX_STEPS = int(os.environ.get("ANIMICA_AGENT_MAX_STEPS", "10"))
MAX_FILE_BYTES = 60_000

app = FastAPI(title="Animica Free AI — GitHub Agent", version="1.0.0")


# ----------------------------- models ----------------------------- #
class ReposReq(BaseModel):
    token: str


class RunReq(BaseModel):
    token: str
    repo: str                       # "owner/name"
    instruction: str
    model: Optional[str] = None
    base_branch: Optional[str] = None


# ----------------------------- helpers ---------------------------- #
def gh_headers(token: str) -> dict:
    return {
        "Authorization": f"Bearer {token}",
        "Accept": "application/vnd.github+json",
        "X-GitHub-Api-Version": "2022-11-28",
        "User-Agent": "animica-dev-agent",
    }


async def gh(client: httpx.AsyncClient, method: str, path: str, token: str, **kw) -> httpx.Response:
    r = await client.request(method, GH_API + path, headers=gh_headers(token), timeout=30, **kw)
    return r


async def llm(client: httpx.AsyncClient, model: str, messages: list[dict]) -> str:
    headers = {"Content-Type": "application/json"}
    if GATEWAY_KEY:
        headers["Authorization"] = f"Bearer {GATEWAY_KEY}"
    r = await client.post(
        GATEWAY.rstrip("/") + "/chat/completions",
        headers=headers,
        json={"model": model, "messages": messages, "temperature": 0.2, "max_tokens": 4096},
        timeout=300,
    )
    r.raise_for_status()
    d = r.json()
    return d["choices"][0]["message"]["content"]


# Prefer higher-quality tiers, but run on ANY tier a worker is actually serving.
_MODEL_PRIORITY = ["animica-chat-flagship", "animica-chat", "animica-chat-small"]


async def serving_models(client: httpx.AsyncClient) -> set:
    """Model ids a worker will currently pick up (serving!=false from /v1/models)."""
    try:
        r = await client.get(GATEWAY.rstrip("/") + "/models", timeout=12)
        r.raise_for_status()
        return {m["id"] for m in r.json().get("data", []) if m.get("serving") is not False}
    except Exception:   # noqa: BLE001
        return set()


async def pick_serving_model(client: httpx.AsyncClient, preferred: Optional[str]) -> str:
    """Resolve to a serving tier: the requested one if live, else the best serving
    tier available, else best-effort fall back so we never hard-fail on a probe miss."""
    avail = await serving_models(client)
    if not avail:
        return preferred or DEFAULT_MODEL
    if preferred and preferred in avail:
        return preferred
    for m in _MODEL_PRIORITY:
        if m in avail:
            return m
    return sorted(avail)[0]


def parse_action(text: str) -> dict:
    """Extract the first JSON object from a model turn."""
    m = re.search(r"\{.*\}", text, re.DOTALL)
    if not m:
        return {"action": "answer", "args": {"text": text.strip()[:2000]}}
    blob = m.group(0)
    # tolerate ```json fences already stripped by the regex bounds
    try:
        return json.loads(blob)
    except Exception:
        # try to trim to the outermost balanced braces
        depth = 0
        for i, ch in enumerate(blob):
            if ch == "{":
                depth += 1
            elif ch == "}":
                depth -= 1
                if depth == 0:
                    try:
                        return json.loads(blob[: i + 1])
                    except Exception:
                        break
        return {"action": "answer", "args": {"text": text.strip()[:2000]}}


SYSTEM = """You are the Animica coding agent. You edit a GitHub repository to satisfy the user's instruction, then open a pull request.

Respond with EXACTLY ONE JSON object per turn and nothing else. Shape:
{"thought": "<short reasoning>", "action": "<name>", "args": { ... }}

Actions:
- {"action":"list_files","args":{}}                      list the repository file tree
- {"action":"read_file","args":{"path":"<path>"}}         read a file's contents
- {"action":"write_file","args":{"path":"<path>","content":"<FULL new file contents>"}}  stage a file (create or fully replace)
- {"action":"finalize","args":{"title":"<PR title>","body":"<PR description>"}}  commit all staged files to a new branch and open a PR
- {"action":"answer","args":{"text":"<message>"}}          reply without changing the repo

Rules:
- Inspect before editing: list_files, then read the files you will change.
- write_file always provides the COMPLETE file, not a diff.
- Keep changes minimal and correct. Stage every file you change with write_file, then finalize once.
- Never invent file paths — only write paths you have seen or that clearly should be created.
- One JSON object per turn. No prose outside the JSON."""


# ----------------------------- routes ----------------------------- #
@app.get("/agent/health")
async def health():
    return {"status": "ok", "model": DEFAULT_MODEL, "gateway": GATEWAY}


@app.post("/agent/github/repos")
async def repos(req: ReposReq):
    async with httpx.AsyncClient() as client:
        r = await gh(client, "GET", "/user/repos?sort=updated&per_page=100&affiliation=owner,collaborator", req.token)
        if r.status_code == 401:
            raise HTTPException(401, "GitHub token rejected. Use a fine-grained token with Contents + Pull requests write.")
        if r.status_code >= 400:
            raise HTTPException(r.status_code, f"GitHub error: {r.text[:200]}")
        me = await gh(client, "GET", "/user", req.token)
        login = me.json().get("login") if me.status_code < 400 else None
        out = [
            {"full_name": x["full_name"], "private": x["private"], "default_branch": x["default_branch"],
             "description": x.get("description"), "updated_at": x.get("updated_at")}
            for x in r.json()
        ]
        return {"login": login, "repos": out}


@app.post("/agent/run")
async def run(req: RunReq):
    if "/" not in req.repo:
        raise HTTPException(400, "repo must be 'owner/name'")
    owner, name = req.repo.split("/", 1)
    staged: dict[str, str] = {}
    transcript: list[dict] = []

    async with httpx.AsyncClient() as client:
        model = await pick_serving_model(client, req.model or DEFAULT_MODEL)
        # repo metadata
        meta = await gh(client, "GET", f"/repos/{owner}/{name}", req.token)
        if meta.status_code >= 400:
            raise HTTPException(meta.status_code, f"Cannot access repo: {meta.text[:160]}")
        base = req.base_branch or meta.json().get("default_branch", "main")

        # cached tree
        tree_cache: Optional[list[str]] = None

        async def get_tree() -> list[str]:
            nonlocal tree_cache
            if tree_cache is not None:
                return tree_cache
            t = await gh(client, "GET", f"/repos/{owner}/{name}/git/trees/{base}?recursive=1", req.token)
            paths = [e["path"] for e in t.json().get("tree", []) if e.get("type") == "blob"] if t.status_code < 400 else []
            tree_cache = paths[:800]
            return tree_cache

        messages = [
            {"role": "system", "content": SYSTEM},
            {"role": "user", "content": f"Repository: {req.repo} (base branch: {base}).\nInstruction: {req.instruction}\n\nBegin. Respond with one JSON action."},
        ]

        pr_url = None
        final_text = None
        for step in range(MAX_STEPS):
            try:
                raw = await llm(client, model, messages)
            except httpx.HTTPError as e:
                raise HTTPException(502, f"AI gateway error: {e}")
            act = parse_action(raw)
            action = str(act.get("action", "answer"))
            args = act.get("args", {}) or {}
            thought = str(act.get("thought", ""))[:400]
            transcript.append({"step": step + 1, "thought": thought, "action": action,
                               "args": {k: (v if k != "content" else f"<{len(str(v))} chars>") for k, v in args.items()}})
            messages.append({"role": "assistant", "content": raw})

            if action == "list_files":
                obs = "Repository files:\n" + "\n".join(await get_tree())
            elif action == "read_file":
                path = str(args.get("path", "")).lstrip("/")
                fr = await gh(client, "GET", f"/repos/{owner}/{name}/contents/{path}?ref={base}", req.token)
                if fr.status_code >= 400:
                    obs = f"read_file error: {path} not found ({fr.status_code})."
                else:
                    j = fr.json()
                    content = base64.b64decode(j.get("content", "")).decode("utf-8", "replace")[:MAX_FILE_BYTES]
                    obs = f"Contents of {path}:\n{content}"
            elif action == "write_file":
                path = str(args.get("path", "")).lstrip("/")
                content = args.get("content", "")
                if not path or content is None:
                    obs = "write_file error: need path and content."
                else:
                    staged[path] = content if isinstance(content, str) else json.dumps(content)
                    obs = f"Staged {path} ({len(staged[path])} chars). Staged files: {list(staged)}"
            elif action == "finalize":
                if not staged:
                    obs = "finalize error: nothing staged. Use write_file first."
                else:
                    pr_url = await _commit_and_pr(client, req.token, owner, name, base, staged,
                                                  str(args.get("title") or req.instruction[:60]),
                                                  str(args.get("body") or "Automated change by the Animica coding agent."))
                    final_text = f"Opened pull request: {pr_url}"
                    break
            elif action == "answer":
                final_text = str(args.get("text", "")).strip()
                break
            else:
                obs = f"Unknown action '{action}'. Use list_files, read_file, write_file, finalize, or answer."

            messages.append({"role": "user", "content": f"Observation:\n{obs[:8000]}"})
        else:
            final_text = "Reached the step limit before finishing. Staged files: " + (", ".join(staged) or "none")

        return JSONResponse({
            "repo": req.repo, "base_branch": base, "model": model,
            "staged_files": list(staged), "pr_url": pr_url,
            "answer": final_text, "transcript": transcript,
        })


async def _commit_and_pr(client, token, owner, name, base, staged, title, body) -> str:
    # base sha
    ref = await gh(client, "GET", f"/repos/{owner}/{name}/git/ref/heads/{base}", token)
    if ref.status_code >= 400:
        raise HTTPException(400, f"Cannot read base ref: {ref.text[:160]}")
    base_sha = ref.json()["object"]["sha"]
    branch = f"animica-agent/{_slug(title)}"
    # create branch (retry with suffix if exists)
    cr = await gh(client, "POST", f"/repos/{owner}/{name}/git/refs", token,
                  json={"ref": f"refs/heads/{branch}", "sha": base_sha})
    if cr.status_code == 422:
        branch = branch + "-2"
        cr = await gh(client, "POST", f"/repos/{owner}/{name}/git/refs", token,
                      json={"ref": f"refs/heads/{branch}", "sha": base_sha})
    if cr.status_code >= 400:
        raise HTTPException(400, f"Cannot create branch: {cr.text[:160]}")
    # commit each staged file via contents API
    for path, content in staged.items():
        cur = await gh(client, "GET", f"/repos/{owner}/{name}/contents/{path}?ref={branch}", token)
        sha = cur.json().get("sha") if cur.status_code < 400 else None
        payload = {"message": f"{title}\n\nvia Animica coding agent", "branch": branch,
                   "content": base64.b64encode(content.encode("utf-8")).decode("ascii")}
        if sha:
            payload["sha"] = sha
        pr = await gh(client, "PUT", f"/repos/{owner}/{name}/contents/{path}", token, json=payload)
        if pr.status_code >= 400:
            raise HTTPException(400, f"Commit failed for {path}: {pr.text[:160]}")
    # open PR
    pr = await gh(client, "POST", f"/repos/{owner}/{name}/pulls", token,
                  json={"title": title, "head": branch, "base": base,
                        "body": body + "\n\n— opened by the [Animica coding agent](https://animica.dev/#agent)"})
    if pr.status_code >= 400:
        raise HTTPException(400, f"Cannot open PR: {pr.text[:200]}")
    return pr.json().get("html_url", "")


def _slug(s: str) -> str:
    return (re.sub(r"[^a-z0-9]+", "-", s.lower()).strip("-") or "change")[:40]


# ============================ agents-mode (build swarm) ============================ #
SWARM_MAX_FILES = int(os.environ.get("ANIMICA_SWARM_MAX_FILES", "5"))


class SwarmReq(BaseModel):
    prompt: str
    model: Optional[str] = None
    files: Optional[dict[str, str]] = None   # when present → revise this project


class PublishReq(BaseModel):
    token: str
    repo: str
    project: str
    files: dict[str, str]
    base_branch: Optional[str] = None


def _strip_fence(s: str) -> str:
    s = (s or "").strip()
    if s.startswith("```"):
        s = s.split("\n", 1)[1] if "\n" in s else s[3:]
        i = s.rfind("```")
        if i != -1:
            s = s[:i]
    return s.strip()


LEADER_SYS = """You are the LEAD agent of an AI build swarm. Turn the user's request into a SMALL, self-contained web app using ONLY plain HTML/CSS/JS — no build step, no frameworks, no external network or CDNs. Output ONLY one JSON object and nothing else:
{"project":"<short name>","description":"<one sentence>","files":[{"path":"index.html","spec":"<precise, self-contained brief for the worker who writes ONLY this file>"}]}
Rules: 2 to 4 files. ALWAYS include index.html. Prefer index.html + style.css + app.js; index.html MUST reference them by those exact names (<link rel=stylesheet href="style.css">, <script src="app.js"></script>). Each spec fully describes that file so a worker can write it in isolation. Make it genuinely impressive, interactive, and actually runnable by opening index.html."""


def _worker_sys(project: str, desc: str, paths: list[str]) -> str:
    return (f"You are ONE worker in a build swarm. Project: {project} — {desc}. "
            f"The project files are: {', '.join(paths)}. You write exactly ONE of them. "
            "Output ONLY the raw, complete contents of your assigned file — no markdown fences, no commentary. "
            "It MUST integrate with the sibling files (index.html links style.css and app.js by those exact names). "
            "Plain HTML/CSS/JS only; no external resources, CDNs, or network calls.")


def _inline_preview(files: dict[str, str]) -> str:
    """Fold style.css / app.js into index.html so the result runs in an iframe srcdoc."""
    html = files.get("index.html", "") or "<!doctype html><meta charset=utf-8><body>(no index.html produced)</body>"
    css = files.get("style.css", "")
    js = files.get("app.js", "")
    if css:
        new, n = re.subn(r'<link[^>]*href=["\']\.?/?style\.css["\'][^>]*>', f"<style>\n{css}\n</style>", html, flags=re.I)
        html = new if n else (html.replace("</head>", f"<style>\n{css}\n</style></head>", 1) if "</head>" in html else f"<style>\n{css}\n</style>" + html)
    if js:
        new, n = re.subn(r'<script[^>]*src=["\']\.?/?app\.js["\'][^>]*>\s*</script>', f"<script>\n{js}\n</script>", html, flags=re.I)
        html = new if n else (html.replace("</body>", f"<script>\n{js}\n</script></body>", 1) if "</body>" in html else html + f"<script>\n{js}\n</script>")
    return html


REVISE_LEADER_SYS = """You are the LEAD agent revising an existing web app. You are given the current files and a change request from the user. Output ONLY one JSON object and nothing else:
{"project":"<name>","description":"<one sentence>","files":[{"path":"<path>","spec":"<what this file must now contain / how to change it>"}]}
Include ONLY the files that must change or be added (1 to 4). Keep paths consistent with the existing project (index.html, style.css, app.js). Each spec fully describes the file's intended new state so a worker can rewrite it."""


async def _swarm_events(prompt: str, model: Optional[str], existing: Optional[dict] = None):
    def ev(o):
        return "data: " + json.dumps(o) + "\n\n"
    preferred = model or DEFAULT_MODEL
    existing = {k.lstrip("/"): v for k, v in (existing or {}).items() if k and ".." not in k}
    revise = bool(existing)
    async with httpx.AsyncClient() as client:
        model = await pick_serving_model(client, preferred)
        yield ev({"type": "phase", "phase": "planning",
                  "note": "Leader agent is planning the revision…" if revise else "Leader agent is decomposing your request…"})
        if revise:
            cur = "\n".join(f"- {p} ({len(c)} chars)" for p, c in existing.items())
            lead_msgs = [{"role": "system", "content": REVISE_LEADER_SYS},
                         {"role": "user", "content": f"Current files:\n{cur}\n\nChange request: {prompt}"}]
        else:
            lead_msgs = [{"role": "system", "content": LEADER_SYS}, {"role": "user", "content": prompt}]
        try:
            raw = await llm(client, model, lead_msgs)
        except Exception as e:   # noqa: BLE001
            yield ev({"type": "error", "message": f"leader failed: {e}"}); return
        plan = parse_action(_strip_fence(raw)) if "{" in (raw or "") else {}
        clean, seen = [], set()
        for f in (plan.get("files") or [])[:SWARM_MAX_FILES]:
            p = str(f.get("path", "")).strip().lstrip("/")
            if not p or p in seen or ".." in p:
                continue
            seen.add(p); clean.append({"path": p, "spec": str(f.get("spec", ""))})
        if not clean and not revise:
            clean = [{"path": "index.html", "spec": "The complete single-page app for: " + prompt}]
        project = str(plan.get("project") or "app")[:60]
        desc = str(plan.get("description") or prompt[:100])[:200]
        yield ev({"type": "leader", "project": project, "description": desc,
                  "files": [f["path"] for f in clean], "revise": revise})

        built: dict[str, str] = dict(existing)
        allpaths = list(dict.fromkeys([f["path"] for f in clean] + list(existing.keys())))
        wsys = _worker_sys(project, desc, allpaths)
        for f in clean:
            yield ev({"type": "worker", "path": f["path"], "status": "start"})
            umsg = f"Write the file `{f['path']}`.\nSpec: {f['spec']}"
            cur_content = existing.get(f["path"], "")
            if cur_content:
                umsg += (f"\n\nCurrent contents of {f['path']} — rewrite the whole file to satisfy the spec, "
                         f"keeping what still applies:\n{cur_content[:MAX_FILE_BYTES]}")
            try:
                wr = await llm(client, model, [{"role": "system", "content": wsys}, {"role": "user", "content": umsg}])
                content = _strip_fence(wr)
            except Exception as e:   # noqa: BLE001
                content = existing.get(f["path"]) or f"<!-- worker error for {f['path']}: {e} -->"
            built[f["path"]] = content
            yield ev({"type": "worker", "path": f["path"], "status": "done", "bytes": len(content)})
            # LIVE incremental preview — the app takes shape as each file lands
            yield ev({"type": "preview", "preview": _inline_preview(built)})

        yield ev({"type": "phase", "phase": "assembling", "note": "Finalizing the build…"})
        yield ev({"type": "done", "project": project, "description": desc, "files": built,
                  "preview": _inline_preview(built)})


@app.post("/agent/swarm")
async def swarm(req: SwarmReq):
    return StreamingResponse(_swarm_events(req.prompt, req.model, req.files), media_type="text/event-stream",
                             headers={"Cache-Control": "no-cache", "X-Accel-Buffering": "no"})


@app.post("/agent/publish")
async def publish(req: PublishReq):
    """Open a PR containing a set of built files (used by agents-mode 'Open PR')."""
    if "/" not in req.repo:
        raise HTTPException(400, "repo must be 'owner/name'")
    owner, name = req.repo.split("/", 1)
    files = {k.lstrip("/"): v for k, v in (req.files or {}).items() if k and ".." not in k}
    if not files:
        raise HTTPException(400, "no files to publish")
    async with httpx.AsyncClient() as client:
        meta = await gh(client, "GET", f"/repos/{owner}/{name}", req.token)
        if meta.status_code >= 400:
            raise HTTPException(meta.status_code, f"Cannot access repo: {meta.text[:160]}")
        base = req.base_branch or meta.json().get("default_branch", "main")
        pr_url = await _commit_and_pr(client, req.token, owner, name, base, files,
                                      f"Add {req.project} (built by the Animica agent swarm)",
                                      f"The Animica agent swarm built **{req.project}** from a single prompt.\n\nFiles: "
                                      + ", ".join(files))
        return {"pr_url": pr_url, "files": list(files)}

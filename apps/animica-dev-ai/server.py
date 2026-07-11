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
from fastapi.responses import JSONResponse
from pydantic import BaseModel

GATEWAY = os.environ.get("ANIMICA_FREE_AI_BASE", "http://127.0.0.1:8792/v1")
GATEWAY_KEY = os.environ.get("ANIMICA_AI_GATEWAY_API_KEY", "")
GH_API = "https://api.github.com"
DEFAULT_MODEL = os.environ.get("ANIMICA_AGENT_MODEL", "qwen2.5-coder:14b")
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
        json={"model": model, "messages": messages, "temperature": 0.2, "max_tokens": 1024},
        timeout=300,
    )
    r.raise_for_status()
    d = r.json()
    return d["choices"][0]["message"]["content"]


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
    model = req.model or DEFAULT_MODEL
    if "/" not in req.repo:
        raise HTTPException(400, "repo must be 'owner/name'")
    owner, name = req.repo.split("/", 1)
    staged: dict[str, str] = {}
    transcript: list[dict] = []

    async with httpx.AsyncClient() as client:
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

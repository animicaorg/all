#!/usr/bin/env python3
"""free_inference.py — free, keyless AI inference on https://animica.dev/v1.

Usage:  python3 free_inference.py [prompt]

The endpoint is OpenAI-compatible (no API key; rate limit 30 req/min/IP).
Capacity comes from community GPU workers: every model in GET /v1/models
carries a boolean `serving` flag. This script picks a model with
serving=true; if none is serving right now it says so honestly and exits 0
(that is expected some of the time — capacity is community-provided).

Uses the `openai` SDK when installed, plain urllib otherwise.
"""
from __future__ import annotations

import json
import sys
import urllib.error
import urllib.request

BASE = "https://animica.dev/v1"
TIMEOUT = 120  # community GPUs can be slow to first token

CAPACITY_MSG = (
    "No model is currently serving (all `serving` flags are false in "
    f"{BASE}/models). Capacity on animica.dev is provided by community GPU "
    "workers, so it comes and goes — retry later, or run a worker yourself: "
    "pip install animica && animica up"
)


def get_models():
    req = urllib.request.Request(BASE + "/models")
    with urllib.request.urlopen(req, timeout=30) as resp:
        return json.load(resp)["data"]


def pick_model(models) -> str | None:
    serving = [m["id"] for m in models if m.get("serving")]
    return serving[0] if serving else None


def chat_urllib(model: str, prompt: str) -> str:
    body = json.dumps(
        {"model": model, "messages": [{"role": "user", "content": prompt}]}
    ).encode()
    req = urllib.request.Request(
        BASE + "/chat/completions",
        data=body,
        headers={"Content-Type": "application/json"},
    )
    with urllib.request.urlopen(req, timeout=TIMEOUT) as resp:
        return json.load(resp)["choices"][0]["message"]["content"]


def chat_openai(model: str, prompt: str) -> str:
    from openai import OpenAI  # any key string works; the gateway is keyless

    client = OpenAI(base_url=BASE, api_key="none", timeout=TIMEOUT)
    r = client.chat.completions.create(
        model=model, messages=[{"role": "user", "content": prompt}]
    )
    return r.choices[0].message.content


def main() -> int:
    prompt = " ".join(sys.argv[1:]) or "In one sentence: what is a blockchain?"
    try:
        models = get_models()
    except (urllib.error.URLError, TimeoutError, KeyError) as e:
        print(f"error: cannot list models at {BASE}/models: {e}", file=sys.stderr)
        return 1

    print("models:", ", ".join(f"{m['id']}({'up' if m.get('serving') else 'down'})" for m in models))
    model = pick_model(models)
    if model is None:
        print(CAPACITY_MSG)
        return 0

    print(f"asking {model}: {prompt!r}")
    try:
        try:
            import openai  # noqa: F401
            answer = chat_openai(model, prompt)
        except ImportError:
            answer = chat_urllib(model, prompt)
    except urllib.error.HTTPError as e:
        if e.code == 503:
            print("Worker went offline mid-request (503). " + CAPACITY_MSG)
            return 0
        print(f"error: HTTP {e.code}: {e.read(200)!r}", file=sys.stderr)
        return 1
    except Exception as e:  # SDK errors, timeouts — report, don't traceback
        print(f"error: request failed: {e}", file=sys.stderr)
        return 1
    print("\n" + answer.strip())
    return 0


if __name__ == "__main__":
    sys.exit(main())

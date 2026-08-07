"""`animica-hosted` — the OpenAI-compatible endpoint on animica.dev.

Why this provider exists
------------------------
Before it, a fresh `animica chat` on a machine with no wallet and no GPU landed
on the `offline` provider, whose entire behaviour is::

    I'm running in offline mode without a model. I can only echo a short
    static reply.

So the headline command of a downloadable CLI did nothing on first run. The
cascade had three providers and all three needed something the new user did not
have yet: `distributed-aicf` needs a funded wallet, `local-flagship` needs a
multi-gigabyte model bundle, and `offline` needs nothing because it does nothing.

`animica.dev/v1` is already public, already keyless, and already serves
`kimi-k3` from the miner network. This provider puts it in the cascade above
`offline`, so the CLI answers immediately after install and only asks for a
wallet when the user wants the paid distributed path.

Three details that are not obvious
----------------------------------
* **The endpoint returns reasoning inside `content`.** A short completion comes
  back as ``<think>\\nOkay, let's see. The user is asking me to act as`` — the
  model's scratchpad, truncated mid-sentence by `max_tokens`. Printing that as
  the answer would make the CLI look broken. `strip_reasoning()` removes it, and
  keeps the reasoning available under `metadata["reasoning"]` for `--verbose`.
* **No key is required, so there is nothing to leak** — but a key is *accepted*
  (`ANIMICA_API_KEY`) because the same code serves Pro users, whose licence
  raises their rate limit and unlocks the flagship tier.
* **stdlib only.** `urllib` rather than `requests`, so the wheel keeps working
  with no third-party HTTP dependency.
"""

from __future__ import annotations

import json
import os
import re
import time
import urllib.error
import urllib.request
from dataclasses import dataclass
from typing import Optional

from agent_runtime.providers import Provider, TurnRequest, TurnResult

DEFAULT_BASE_URL = "https://animica.dev/v1"
DEFAULT_MODEL = "kimi-k3"
PRICING_HINT = "animica.dev/pricing lifts the limit."
# Not a timeout so much as a dead-socket backstop: the bridge's own ceiling is
# 600s, so anything shorter cancels work the network is still doing.
DEFAULT_TIMEOUT = 660.0

SYSTEM_PROMPT = ("You are the Animica coding assistant, running in a terminal. "
                 "Be concise and concrete. Prefer showing a command or a diff "
                 "over describing one.")

# The endpoint answers 200 with prose when no miner can serve. These are its
# words, matched loosely enough to survive rewording but specifically enough not
# to swallow a genuine answer that happens to discuss the network.
_APOLOGY_MARKERS = (
    "couldn't complete your request",
    "could not complete your request",
    "wasn't able to load a language model",
    "was not able to load a language model",
    "no provider",
)


def _is_capacity_apology(text: str) -> bool:
    if not text:
        return False
    t = text.strip().lower()
    if len(t) > 1200:          # a real answer that long is not the notice
        return False
    hits = sum(1 for m in _APOLOGY_MARKERS if m in t)
    # Two independent markers, or one plus the tell-tale upgrade footer.
    return hits >= 2 or (hits >= 1 and "animica up" in t)

# The tier a free caller gets. Pro raises this; see agent_runtime.entitlements.
FREE_MODEL = "kimi-k3"
PRO_MODEL = "kimi-k3"          # same served model today; Pro raises limits/context

_THINK_BLOCK = re.compile(r"<think>.*?</think>", re.DOTALL | re.IGNORECASE)
_THINK_OPEN = re.compile(r"<think>.*\Z", re.DOTALL | re.IGNORECASE)


def strip_reasoning(text: str) -> tuple[str, str]:
    """Split model output into (answer, reasoning).

    The endpoint emits `<think>…</think>` inline in `content`. A completion cut
    short by `max_tokens` has an OPEN `<think>` and no close, which is the common
    case for the small token budgets the agent loop uses — so an unterminated
    block is treated as reasoning to the end of the string rather than left in
    the answer. Getting this backwards is what made the first test print
    "Okay, let's see. The user is asking me to act as" as its reply.
    """
    if not text:
        return "", ""
    reasoning_parts = _THINK_BLOCK.findall(text)
    answer = _THINK_BLOCK.sub("", text)
    m = _THINK_OPEN.search(answer)
    if m:
        reasoning_parts.append(m.group(0))
        answer = answer[: m.start()]
    reasoning = "\n".join(
        re.sub(r"</?think>", "", p, flags=re.IGNORECASE).strip()
        for p in reasoning_parts
    ).strip()
    return answer.strip(), reasoning


@dataclass
class HostedConfig:
    base_url: str = DEFAULT_BASE_URL
    model: str = DEFAULT_MODEL
    api_key: Optional[str] = None
    timeout: float = DEFAULT_TIMEOUT

    @classmethod
    def from_env(cls) -> "HostedConfig":
        return cls(
            base_url=(os.environ.get("ANIMICA_API_BASE") or DEFAULT_BASE_URL).rstrip("/"),
            model=os.environ.get("ANIMICA_API_MODEL") or DEFAULT_MODEL,
            # Optional: absent means the free keyless tier.
            api_key=os.environ.get("ANIMICA_API_KEY") or None,
            timeout=float(os.environ.get("ANIMICA_API_TIMEOUT") or DEFAULT_TIMEOUT),
        )


class HostedProvider(Provider):
    """Chat via the public OpenAI-compatible endpoint."""

    name = "animica-hosted"

    def __init__(self, config: Optional[HostedConfig] = None, *,
                 entitlements=None) -> None:
        self.cfg = config or HostedConfig.from_env()
        self.entitlements = entitlements
        self._probe: Optional[tuple[bool, str]] = None

    # -- availability --------------------------------------------------------
    def is_available(self) -> tuple[bool, str]:
        """One cached probe of /models.

        Cached because the cascade asks every provider on every turn, and a
        network round-trip per turn to decide whether to do a network round-trip
        is a visible stutter in an interactive REPL.
        """
        if self._probe is not None:
            return self._probe
        try:
            payload = self._get("/models")
            ids = [m.get("id") for m in (payload.get("data") or [])]
            if self.cfg.model in ids:
                self._probe = (True, f"ok ({self.cfg.model})")
            elif ids:
                # Serve with whatever it does offer rather than refusing: a
                # renamed model should not take the CLI offline.
                self.cfg.model = ids[0]
                self._probe = (True, f"ok (using {ids[0]})")
            else:
                self._probe = (False, "endpoint served no models")
        except Exception as exc:  # noqa: BLE001 — any failure means unavailable
            self._probe = (False, f"unreachable: {_short(exc)}")
        return self._probe

    # -- serving -------------------------------------------------------------
    def serve(self, req: TurnRequest) -> TurnResult:
        ok, reason = self.is_available()
        if not ok:
            from agent_runtime.errors import ProviderUnavailable
            raise ProviderUnavailable(f"{self.name}: {reason}")

        messages = []
        for h in req.history or []:
            role = h.get("role")
            content = h.get("content")
            if role in ("user", "assistant", "system") and content:
                messages.append({"role": role, "content": content})
        messages.append({"role": "user", "content": req.prompt})

        if not any(m["role"] == "system" for m in messages):
            messages.insert(0, {"role": "system", "content": SYSTEM_PROMPT})

        body = {
            "model": self.cfg.model,
            "messages": messages,
            "max_tokens": int(req.max_output_tokens or 1024),
            "temperature": float(req.temperature),
            "top_p": float(req.top_p),
            # STREAMING IS NOT OPTIONAL HERE. Inference is served by volunteer
            # miners, and a cold worker can take minutes to answer. The first
            # version of this provider posted without `stream` and gave up after
            # 120s, so it timed out and then reported the endpoint's own
            # "couldn't complete your request" apology as the assistant's reply.
            # Streaming both survives the wait and gives the live token output an
            # interactive CLI is judged on. animica.dev's own client does the
            # same, with a 660s safety net rather than a real timeout.
            "stream": True,
        }

        started = time.monotonic()
        raw, receipt = self._stream("/chat/completions", body,
                                    on_text=req.stream_callback)
        if not raw.strip():
            # The stream produced nothing — a proxy that buffered it, or a worker
            # that dropped. Retry once without streaming rather than reporting an
            # empty answer.
            body.pop("stream", None)
            payload = self._post("/chat/completions", body)
            choices = payload.get("choices") or []
            if choices:
                raw = ((choices[0] or {}).get("message") or {}).get("content") or ""
            receipt = receipt or payload.get("animica_receipt")
        latency_ms = int((time.monotonic() - started) * 1000)

        answer, reasoning = strip_reasoning(raw)

        # The endpoint answers 200 with a human-readable apology when no miner
        # could load a model. Left undetected it becomes the assistant's reply and
        # the cascade never falls through to another provider, so it is treated as
        # unavailability — which is what it is.
        if _is_capacity_apology(answer):
            from agent_runtime.errors import ProviderUnavailable
            raise ProviderUnavailable(
                f"{self.name}: no miner is serving {self.cfg.model} right now "
                "(the network returned its capacity notice)"
            )

        # An empty answer with reasoning present means the token budget was spent
        # entirely on thinking. Saying so is more useful than printing nothing.
        if not answer and reasoning:
            answer = ("[the model spent its whole token budget reasoning; "
                      "retry with a larger --max-output-tokens]")

        return TurnResult(
            text=answer,
            provider=self.name,
            tier=self.cfg.model,
            requested_tier=req.tier_preferred,
            effective_mode="hosted",
            cost_animica=0.0,          # free tier costs no ANM
            latency_ms=latency_ms,
            metadata={
                "model": self.cfg.model,
                "reasoning": reasoning,
                "receipt": receipt,
                "endpoint": self.cfg.base_url,
                "keyed": bool(self.cfg.api_key),
            },
        )

    # -- transport -----------------------------------------------------------
    def _headers(self) -> dict:
        h = {"content-type": "application/json",
             "user-agent": "animica-cli/hosted"}
        if self.cfg.api_key:
            h["authorization"] = f"Bearer {self.cfg.api_key}"
        return h

    def _get(self, path: str):
        req = urllib.request.Request(
            f"{self.cfg.base_url}{path}", headers=self._headers(), method="GET")
        with urllib.request.urlopen(req, timeout=min(self.cfg.timeout, 20.0)) as r:
            return json.loads(r.read().decode("utf-8"))

    def _stream(self, path: str, body: dict, *, on_text=None) -> tuple[str, object]:
        """POST with `stream: true` and read Server-Sent Events.

        Returns the full accumulated text and any `animica_receipt` seen. Reasoning
        inside `<think>` is withheld from `on_text` as it arrives, so the terminal
        shows the answer rather than the model's scratchpad — but it is kept in the
        returned text so the caller can still surface it under --verbose.
        """
        data = json.dumps(body).encode("utf-8")
        req = urllib.request.Request(
            f"{self.cfg.base_url}{path}", data=data,
            headers={**self._headers(), "accept": "text/event-stream"},
            method="POST")

        acc: list[str] = []
        receipt = None
        in_think = False
        try:
            with urllib.request.urlopen(req, timeout=self.cfg.timeout) as r:
                pending = ""
                for rawline in r:
                    line = rawline.decode("utf-8", "replace")
                    if not line.strip():
                        continue
                    if not line.startswith("data:"):
                        continue
                    payload = line[5:].strip()
                    if not payload or payload == "[DONE]":
                        continue
                    try:
                        j = json.loads(payload)
                    except ValueError:
                        continue
                    if j.get("animica_receipt"):
                        receipt = j["animica_receipt"]
                    choices = j.get("choices") or []
                    if not choices:
                        continue
                    delta = (choices[0] or {}).get("delta") or {}
                    piece = delta.get("content")
                    if not piece:
                        continue
                    acc.append(piece)
                    if on_text is None:
                        continue
                    # Emit only what is outside a <think> block. Tags can be split
                    # across deltas, so a small pending buffer holds a partial tag
                    # rather than printing "<thi".
                    pending += piece
                    while pending:
                        if in_think:
                            end = pending.lower().find("</think>")
                            if end == -1:
                                if "<" in pending[-8:]:
                                    break
                                pending = ""
                                break
                            pending = pending[end + len("</think>"):]
                            in_think = False
                            continue
                        start = pending.lower().find("<think>")
                        if start == -1:
                            if "<" in pending[-8:]:
                                safe, pending = pending[:-8], pending[-8:]
                            else:
                                safe, pending = pending, ""
                            if safe:
                                try:
                                    on_text(safe)
                                except Exception:  # noqa: BLE001
                                    pass
                            break
                        safe = pending[:start]
                        if safe:
                            try:
                                on_text(safe)
                            except Exception:  # noqa: BLE001
                                pass
                        pending = pending[start + len("<think>"):]
                        in_think = True
        except urllib.error.HTTPError as exc:
            self._raise_http(exc)
        except Exception as exc:  # noqa: BLE001
            # Partial output is still worth returning: a dropped socket after 300
            # tokens should not discard them.
            if acc:
                return "".join(acc), receipt
            from agent_runtime.errors import ProviderUnavailable
            raise ProviderUnavailable(f"{self.name}: {_short(exc)}") from exc
        return "".join(acc), receipt

    def _raise_http(self, exc) -> None:
        detail = ""
        try:
            detail = exc.read().decode("utf-8")[:400]
        except Exception:  # noqa: BLE001
            pass
        from agent_runtime.errors import ProviderUnavailable
        if exc.code == 429:
            raise ProviderUnavailable(
                f"{self.name}: rate limited by the free tier. "
                f"{PRICING_HINT}") from exc
        raise ProviderUnavailable(
            f"{self.name}: HTTP {exc.code}{': ' + detail if detail else ''}") from exc

    def _post(self, path: str, body: dict):
        data = json.dumps(body).encode("utf-8")
        req = urllib.request.Request(
            f"{self.cfg.base_url}{path}", data=data,
            headers=self._headers(), method="POST")
        try:
            with urllib.request.urlopen(req, timeout=self.cfg.timeout) as r:
                return json.loads(r.read().decode("utf-8"))
        except urllib.error.HTTPError as exc:
            self._raise_http(exc)
        except Exception as exc:  # noqa: BLE001
            from agent_runtime.errors import ProviderUnavailable
            raise ProviderUnavailable(f"{self.name}: {_short(exc)}") from exc


def _short(exc: Exception, limit: int = 120) -> str:
    s = str(exc) or exc.__class__.__name__
    return s if len(s) <= limit else s[:limit] + "…"


__all__ = ["HostedProvider", "HostedConfig", "strip_reasoning",
           "DEFAULT_BASE_URL", "DEFAULT_MODEL"]

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


# A worker that cannot load a model answers with one of these markers instead of
# prose. The bridge usually swallows them, but not always, and reporting one as
# the assistant's reply is worse than retrying.
# Kept in sync with the bridge's own list (apps/animica-chat/bridge/server.py
# `_STUB_MARKERS`). It is a hand-maintained mirror, and it HAD ALREADY DRIFTED:
# the client did not know `model_load_failed` or `Unrecognized model in`, which a
# worker that picked a diffusion model as an LLM returns AS ITS ANSWER. Anything
# missing here gets printed to the user as the model's considered reply.
_STUB_MARKERS = (
    "[aicf-miner-stub",
    "[distributed-aicf stub",
    "no external workers have claimed",
    "placeholder so the protocol round-trip",
    "model_load_failed",
    "unrecognized model in",
    "no provider could serve",
)

# How much of a stream to hold before deciding it is a real answer. The apology
# and every stub marker announce themselves in their opening words, so this is
# enough to tell them apart — and holding it back is what lets a retry happen
# WITHOUT the failed attempt's text already being on screen.
GATE_CHARS = 96


def _looks_like_failure(text: str) -> bool:
    """True if this output is the network declining, not an answer."""
    if not text:
        return False
    t = text.strip().lower()
    if any(m in t for m in _STUB_MARKERS):
        return True
    return _is_capacity_apology(text)


# Phrases that identify a refusal from its OPENING WORDS alone. `_is_capacity_apology`
# deliberately demands two markers before condemning a full answer, but the gate only
# ever sees the first ~96 characters — and the apology's opening contains exactly one
# ("couldn't complete your request"). Requiring two here would release it on screen.
# NOT the bare phrase "could not complete your request" — a model can legitimately
# say that ("I could not complete your request because that file does not exist"),
# and suppressing it would retry a deterministic prompt into the identical
# suppression three times and then report "no answer" for a turn that DID answer.
# The real notice names the network, so require that co-occurrence; the stub
# markers are unambiguous on their own.
_OPENING_TELLS = (
    "animica ai network couldn't complete",
    "animica ai network could not complete",
    "[aicf-miner-stub",
    "[distributed-aicf stub",
    "model_load_failed",
    "unrecognized model in",
)


def _gate_verdict(buf: str, *, final: bool) -> Optional[bool]:
    """Decide whether buffered stream output may be shown.

    True to release, False to suppress and retry, None for "not enough yet".
    Only the opening of a stream is judged — that is where a refusal names
    itself — and once released the rest flows straight through untouched.
    """
    low = buf.strip().lower()
    if any(tell in low for tell in _OPENING_TELLS):
        return False
    if final:
        return not _looks_like_failure(buf)
    if len(buf.strip()) >= GATE_CHARS:
        return True
    return None

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


# Re-submitting is the ONLY way a client can change which miner serves it: the node
# hands each fresh job to whoever claims it first, so attempt 2 is a different
# miner in all but name. Measured 2026-08-08: of two back-to-back requests, the
# first spent 129s on a worker that could not load a model and then returned the
# capacity notice, and the second was answered by a healthy miner in 75s. One
# attempt therefore fails about as often as it succeeds, and giving up after it is
# the whole reason the CLI looked broken.
DEFAULT_ATTEMPTS = int(os.environ.get("ANIMICA_API_ATTEMPTS") or 4)
# Total wall clock across every attempt. Bounded because each attempt costs the
# bridge's wallet a fresh on-chain job, and an unbounded client retry loop is a
# hang that also amplifies load on a network that is already short of workers.
DEFAULT_DEADLINE = float(os.environ.get("ANIMICA_API_DEADLINE") or 900.0)
# Pause between attempts, BY REASON. A single flat pause was wrong in both
# directions, and the numbers come from the server's own constants:
#
#   * The bridge fails fast for BRIDGE_NEG_TTL_S = 15s after a failure. A 3s pause
#     meant attempts 2, 3 and 4 all landed at t=3/6/9s INSIDE that window and were
#     refused without ever reaching a miner — three attempts spent on nothing. So
#     a "no worker serving" 503 waits past it.
#   * animica.dev rate-limits /v1 at 30r/m burst 12. A 429 is OUR doing, not a
#     miner verdict, so it waits longer AND does not consume a miner attempt (a
#     12-iteration agentic task at 4 attempts is 48 requests; the CLI can trip its
#     own limit).
#   * A cold miner needs no pause at all — the wait IS the work.
RETRY_PAUSE_S = 3.0
NEG_CACHE_CLEAR_S = 18.0
# Wall clock for ONE attempt, and the least time in which an attempt is worth
# starting at all.
ATTEMPT_CAP_S = float(os.environ.get("ANIMICA_API_ATTEMPT_CAP") or 330.0)
MIN_ATTEMPT_S = 120.0
RATE_LIMIT_PAUSE_S = 30.0


def _retry_delay(reason: str) -> float:
    r = (reason or "").lower()
    if "rate limited" in r or "429" in r:
        return RATE_LIMIT_PAUSE_S
    if "no worker serving" in r or "retry shortly" in r:
        return NEG_CACHE_CLEAR_S
    if "reasoning" in r or "cut off" in r:
        return 0.0          # our budget or a dead socket; nothing to wait for
    return RETRY_PAUSE_S


def _is_self_inflicted(reason: str) -> bool:
    """True when the failure was our own rate limit, so it must not cost an attempt."""
    r = (reason or "").lower()
    return "rate limited" in r or "429" in r


@dataclass
class HostedConfig:
    base_url: str = DEFAULT_BASE_URL
    model: str = DEFAULT_MODEL
    api_key: Optional[str] = None
    timeout: float = DEFAULT_TIMEOUT
    attempts: int = DEFAULT_ATTEMPTS
    deadline: float = DEFAULT_DEADLINE

    @classmethod
    def from_env(cls) -> "HostedConfig":
        return cls(
            base_url=(os.environ.get("ANIMICA_API_BASE") or DEFAULT_BASE_URL).rstrip("/"),
            model=os.environ.get("ANIMICA_API_MODEL") or DEFAULT_MODEL,
            # Optional: absent means the free keyless tier.
            api_key=os.environ.get("ANIMICA_API_KEY") or None,
            timeout=float(os.environ.get("ANIMICA_API_TIMEOUT") or DEFAULT_TIMEOUT),
            attempts=max(1, DEFAULT_ATTEMPTS),
            deadline=DEFAULT_DEADLINE,
        )

    @classmethod
    def fallback_from_env(cls) -> "Optional[HostedConfig]":
        """A second OpenAI-compatible endpoint to try when the miner network cannot.

        OFF unless the user configures it, and deliberately so. The obvious
        candidate is the pool API, which is faster and steadier than the miner
        network — but it requires a key that is sha256-hashed at rest and shown
        once, so it exists only if its owner minted one. Inventing or hunting for
        credentials would be worse than being unavailable, so this reads env and
        nothing else::

            ANIMICA_FALLBACK_BASE=https://api.animica.org/v1
            ANIMICA_FALLBACK_KEY=anm_live_…
            ANIMICA_FALLBACK_MODEL=anm-fast-8b     # optional

        Answers from here are labelled with their real endpoint and model, because
        a different model answering must never look like the one you asked for.
        """
        base = (os.environ.get("ANIMICA_FALLBACK_BASE") or "").strip().rstrip("/")
        if not base:
            return None
        return cls(
            base_url=base,
            model=(os.environ.get("ANIMICA_FALLBACK_MODEL") or "anm-fast-8b").strip(),
            api_key=os.environ.get("ANIMICA_FALLBACK_KEY") or None,
            timeout=float(os.environ.get("ANIMICA_API_TIMEOUT") or DEFAULT_TIMEOUT),
            # One shot: it is the safety net, not another place to grind.
            attempts=2,
            deadline=DEFAULT_DEADLINE,
        )


def _reasoning_only_error():
    """`ProviderUnavailable` subclass, defined lazily to avoid a circular import."""
    from agent_runtime.errors import ProviderUnavailable

    class ReasoningOnly(ProviderUnavailable):
        """The model thought until it ran out of tokens. Retry with more, not elsewhere."""

    return ReasoningOnly


_ReasoningOnly = _reasoning_only_error()


def _truncated_error():
    from agent_runtime.errors import ProviderUnavailable

    class Truncated(ProviderUnavailable):
        """The stream died mid-answer. Ask again; do not show the fragment."""

    return Truncated


_Truncated = _truncated_error()


class _StreamGate:
    """Hold the opening of a stream back until it proves to be an answer.

    Without this, retrying is pointless from the user's side: attempt 1's
    capacity notice would already be on the terminal when attempt 2 starts, and
    the two would read as one confused reply. So nothing is shown until the
    opening words rule out a refusal — a delay of a few tokens, once, against
    turns that take half a minute.

    A refusal closes the sink permanently for this attempt. The retry gets a
    fresh gate.
    """

    def __init__(self, sink) -> None:
        self.sink = sink
        self.buf = ""
        self.open = False
        self.suppressed = False

    def feed(self, text: str) -> None:
        if self.sink is None or self.suppressed:
            return
        if self.open:
            self.sink(text)
            return
        self.buf += text
        verdict = _gate_verdict(self.buf, final=False)
        if verdict is True:
            self.open = True
            self.sink(self.buf)
            self.buf = ""
        elif verdict is False:
            self.suppressed = True
            self.buf = ""

    def finish(self) -> None:
        """Release a short answer that ended before the gate had enough to judge."""
        if self.sink is None or self.open or self.suppressed or not self.buf:
            self.buf = ""
            return
        if _gate_verdict(self.buf, final=True):
            self.sink(self.buf)
            self.open = True
        else:
            self.suppressed = True
        self.buf = ""


class HostedProvider(Provider):
    """Chat via the public OpenAI-compatible endpoint."""

    name = "animica-hosted"

    def __init__(self, config: Optional[HostedConfig] = None, *,
                 entitlements=None) -> None:
        self.cfg = config or HostedConfig.from_env()
        self.entitlements = entitlements
        self._probe: Optional[tuple[bool, str]] = None
        # None = not looked yet, False = looked and none configured.
        self._fallback_provider = None
        # (asked_for, got) when the endpoint does not serve the requested model.
        self.substituted_model: Optional[tuple[str, str]] = None

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
                # renamed model should not take the CLI offline. But SAY SO — the
                # banner otherwise keeps claiming kimi-k3 while a different model
                # answers, which is the same dishonesty as an unlabelled fallback.
                self.substituted_model = (self.cfg.model, ids[0])
                self.cfg.model = ids[0]
                self._probe = (True, f"ok (using {ids[0]})")
            else:
                self._probe = (False, "endpoint served no models")
        except Exception as exc:  # noqa: BLE001 — any failure means unavailable
            self._probe = (False, f"unreachable: {_short(exc)}")
        return self._probe

    # -- serving -------------------------------------------------------------
    def serve(self, req: TurnRequest) -> TurnResult:
        """Answer the turn, re-submitting to a different miner while it takes.

        A single attempt is close to a coin flip on this network, so one attempt
        is not a verdict. Each retry is a fresh on-chain job that whichever
        worker is free claims, which is as close to "try another miner" as any
        client can get — the node, not the caller, does the assigning.
        """
        from agent_runtime.errors import ProviderUnavailable

        attempts = max(1, int(getattr(self.cfg, "attempts", 1) or 1))
        deadline = time.monotonic() + float(getattr(self.cfg, "deadline", 0) or 0)
        on_retry = getattr(req, "on_retry", None)
        last: Optional[Exception] = None

        boost = 1
        attempts_used = 0
        # How many times a self-inflicted 429 may be forgiven before it counts.
        refunds = 2
        attempt = 0
        while attempts_used < attempts:
            attempt += 1
            attempts_used += 1
            try:
                # Never start an attempt that cannot finish: it only costs the
                # bridge's wallet another on-chain job.
                left = deadline - time.monotonic()
                if attempts_used > 1 and left < MIN_ATTEMPT_S:
                    break
                return self._serve_once(
                    req, attempt=attempt, of=attempts, token_boost=boost,
                    attempt_cap=min(ATTEMPT_CAP_S, left) if left > 0 else ATTEMPT_CAP_S)
            except ProviderUnavailable as exc:
                last = exc
                if isinstance(exc, _ReasoningOnly):
                    # Not the miner's fault: it ran out of room mid-thought. Give
                    # the next attempt more room rather than hoping for a terser
                    # miner, which is what a plain re-submit would be betting on.
                    boost = min(boost * 2, 8)
                reason = str(exc)
                # A 429 is self-inflicted, so refund the attempt rather than
                # spending the turn's budget on our own rate limiter.
                if _is_self_inflicted(reason) and refunds > 0:
                    refunds -= 1
                    attempts_used -= 1
                if attempts_used >= attempts:
                    break
                pause = _retry_delay(reason)
                if deadline - time.monotonic() <= pause:
                    break
                if callable(on_retry):
                    try:
                        on_retry(attempts_used, attempts, reason)
                    except Exception:  # noqa: BLE001 — display must not end a turn
                        pass
                if pause:
                    time.sleep(pause)
                # A refused attempt says nothing about whether the ENDPOINT is up,
                # so clear the cached probe: otherwise one bad draw at start-up
                # marks the provider unavailable for the whole session.
                self._probe = None

        # Every attempt on the miner network is spent. If the operator configured a
        # second endpoint, try it now — clearly labelled, because an answer from
        # somewhere else must never look like an answer from here.
        alt = self._fallback()
        if alt is not None:
            try:
                r = alt.serve(req)
            except ProviderUnavailable:
                pass
            else:
                r.metadata = dict(r.metadata or {})
                r.metadata["fallback"] = True
                r.metadata["fallback_from"] = self.cfg.model
                return r

        # Re-raise the LAST real reason rather than a summary: "no miner had a
        # model loaded" tells the user something, "failed" does not.
        if last is not None:
            raise last
        raise ProviderUnavailable(self.name, "no attempt produced an answer")

    def _fallback(self) -> "Optional[HostedProvider]":
        """The configured secondary endpoint, built once, or None."""
        if self._fallback_provider is False:          # already looked, none set
            return None
        if self._fallback_provider is not None:
            return self._fallback_provider
        cfg = HostedConfig.fallback_from_env()
        if cfg is None or cfg.base_url.rstrip("/") == self.cfg.base_url.rstrip("/"):
            self._fallback_provider = False
            return None
        alt = HostedProvider(cfg)
        alt.name = f"animica-fallback({cfg.base_url})"
        self._fallback_provider = alt
        return alt

    def _serve_once(self, req: TurnRequest, *, attempt: int = 1,
                    of: int = 1, token_boost: int = 1,
                    attempt_cap: Optional[float] = None) -> TurnResult:
        ok, reason = self.is_available()
        if not ok:
            from agent_runtime.errors import ProviderUnavailable
            raise ProviderUnavailable(self.name, reason)

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
            # token_boost only ever grows, and only after a turn came back as
            # pure reasoning with no answer. Capped by the loop at 8x.
            "max_tokens": int((req.max_output_tokens or 1024) * max(1, token_boost)),
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
        # The stream is GATED: output is buffered until its opening proves it is an
        # answer rather than the network's capacity notice. Without this a retry
        # cannot be silent — attempt 1's apology would already be on screen when
        # attempt 2 starts, and the user would read both as one reply.
        gate = _StreamGate(req.stream_callback)
        streamed = self._stream("/chat/completions", body,
                                on_text=gate.feed if req.stream_callback else None,
                                attempt_cap=attempt_cap)
        raw, receipt = streamed[0], streamed[1]
        # A transport that does not report completeness is taken at its word.
        complete = streamed[2] if len(streamed) > 2 else True
        gate.finish()
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
        # A stream that ended without the endpoint saying it was finished is a cut
        # cable, not a short answer. Returning it would put a sentence that stops
        # mid-clause in front of the user as the model's considered reply.
        if not complete and answer.strip():
            raise _Truncated(
                self.name,
                f"attempt {attempt} was cut off before the answer finished")

        if _looks_like_failure(answer) or _looks_like_failure(raw):
            from agent_runtime.errors import ProviderUnavailable
            raise ProviderUnavailable(
                self.name,
                f"the miner that picked up attempt {attempt} could not load a model")

        # A turn whose entire output was reasoning has no answer to show. This is a
        # token-budget failure, not a miner failure, so the retry loop reacts to it
        # by ENLARGING the budget — re-submitting the same small budget to another
        # miner would just buy the same truncated thought again.
        if not answer.strip() and reasoning.strip():
            raise _ReasoningOnly(
                self.name,
                f"attempt {attempt} spent its whole token budget reasoning "
                "without answering")

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

    def _stream(self, path: str, body: dict, *, on_text=None,
                attempt_cap: Optional[float] = None):
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
        # Whether the endpoint told us it was finished. A dropped socket that
        # returns 300 tokens looks exactly like a complete short answer, so
        # without this the CLI presents a sentence cut off mid-clause as the
        # model's finished reply — fabrication by omission, and the most
        # deniable kind. The bridge sends both a finish_reason and [DONE].
        saw_done = False
        saw_finish = False
        in_think = False
        # `urlopen(timeout=)` is a PER-READ idle timeout, not a wall clock, and the
        # bridge sends `: keepalive` every 12s — so a socket that will never produce
        # an answer still never idles out, and a "660s backstop" could not fire at
        # all. The only enforceable bound is checked here, per line.
        cap = float(attempt_cap or self.cfg.timeout)
        began = time.monotonic()
        try:
            with urllib.request.urlopen(req, timeout=min(self.cfg.timeout, 90.0)) as r:
                pending = ""
                for rawline in r:
                    if time.monotonic() - began > cap:
                        # Give back whatever really arrived, marked INCOMPLETE so
                        # the caller retries instead of presenting a fragment.
                        return "".join(acc), receipt, False
                    line = rawline.decode("utf-8", "replace")
                    if not line.strip():
                        continue
                    if not line.startswith("data:"):
                        continue
                    payload = line[5:].strip()
                    if payload == "[DONE]":
                        saw_done = True
                        continue
                    if not payload:
                        continue
                    try:
                        j = json.loads(payload)
                    except ValueError:
                        continue
                    for _c in (j.get("choices") or []):
                        if _c.get("finish_reason"):
                            saw_finish = True
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
                return "".join(acc), receipt, (saw_done or saw_finish)
            from agent_runtime.errors import ProviderUnavailable
            raise ProviderUnavailable(self.name, _short(exc)) from exc
        return "".join(acc), receipt, (saw_done or saw_finish)

    def _raise_http(self, exc) -> None:
        detail = ""
        try:
            detail = exc.read().decode("utf-8")[:400]
        except Exception:  # noqa: BLE001
            pass
        from agent_runtime.errors import ProviderUnavailable
        if exc.code == 429:
            raise ProviderUnavailable(
                self.name, f"rate limited by the free tier. {PRICING_HINT}") from exc
        raise ProviderUnavailable(
            self.name,
            f"HTTP {exc.code}{': ' + detail if detail else ''}") from exc

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
            raise ProviderUnavailable(self.name, _short(exc)) from exc


def _short(exc: Exception, limit: int = 120) -> str:
    s = str(exc) or exc.__class__.__name__
    return s if len(s) <= limit else s[:limit] + "…"


__all__ = ["HostedProvider", "HostedConfig", "strip_reasoning",
           "DEFAULT_BASE_URL", "DEFAULT_MODEL"]

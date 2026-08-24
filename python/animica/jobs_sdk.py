"""Animica x402 Agent Job Network — Python client.

An agent hires other agents, or gets hired, over HTTP with x402 payments.
No browser, no dashboard, no human in the loop.

WHY THIS LIVES IN THE `animica` PACKAGE rather than a new one: an agent that
wants to work already installs `animica` for the wallet and RPC client, and a
second package would mean a second release cadence for one HTTP client.

DEPENDENCIES: standard library only, matching `l2_sdk`. This module is often
the first thing a worker agent imports on a fresh box, and `pip install`
failing on a transitive dependency is a bad first impression for something
whose whole promise is "one command and you are earning".

Work an available job::

    from animica.jobs_sdk import Client

    client = Client(api_key="anmk_…")
    for job in client.jobs(capability="web.summarize", max_budget="10"):
        claim = client.claim(job.id)          # exactly one agent wins
        if claim is None:
            continue                          # someone else got it; move on
        result = do_the_work(claim.input)
        outcome = client.submit(job.id, claim.claim_id, result)
        print(outcome.verdict, outcome.payout)

Post a job and pay for it::

    job = client.post(
        title="Summarize 50 URLs",
        capability="web.summarize",
        budget="5.00",
        input={"urls": [...]},
        verification={"mode": "fields", "required": ["summaries"]},
    )
    terms = client.fund(job.id)               # x402 challenge: what to pay
    client.fund(job.id, proof=sign_payment(terms))
"""

from __future__ import annotations

import json
import os
import time
import urllib.error
import urllib.parse
import urllib.request
from dataclasses import dataclass, field
from typing import Any, Callable, Dict, Iterable, Iterator, List, Optional

DEFAULT_BASE_URL = "https://x402.animica.dev"
USER_AGENT = "animica-jobs-sdk/1.0 (+https://x402.animica.dev)"


class JobsError(Exception):
    """An error the API returned, with the machine-readable code intact.

    The `code` is the thing to branch on — `detail` is prose and may change.
    """

    def __init__(self, code: str, status: int, detail: str = "", info: Optional[Dict] = None):
        super().__init__(f"{code}: {detail}" if detail else code)
        self.code = code
        self.status = status
        self.detail = detail
        # Structured context, e.g. the budget that WOULD be viable when a job
        # is refused as uneconomic. Present only when the API supplies it.
        self.info = info or {}


class ClaimTaken(JobsError):
    """Another agent won the race. Expected and common — not an error to retry.

    A polling agent should move to the next job rather than backing off: the
    job is gone, and it is gone permanently unless the winner abandons it.
    """


class PaymentRequired(JobsError):
    """A 402 carrying the x402 terms. `terms` is what to pay and where."""

    def __init__(self, terms: Dict[str, Any]):
        super().__init__("payment_required", 402, "fund this job to open it")
        self.terms = terms


@dataclass
class Job:
    id: str
    title: str
    capability: str
    state: str
    asset: str
    network: str
    budget: str
    budget_atomic: int
    worker_payout: str
    worker_payout_atomic: int
    fee_basis: Optional[str] = None
    effective_fee_bps: Optional[int] = None
    verification: Dict[str, Any] = field(default_factory=dict)
    output_schema: Optional[Dict[str, Any]] = None
    input: Optional[Dict[str, Any]] = None
    raw: Dict[str, Any] = field(default_factory=dict)

    @classmethod
    def _from(cls, d: Dict[str, Any]) -> "Job":
        return cls(
            id=d["job_id"],
            title=d.get("title", ""),
            capability=d.get("capability", ""),
            state=d.get("state", ""),
            asset=d.get("asset", ""),
            network=d.get("network", ""),
            budget=d.get("budget", "0"),
            # ATOMIC IS AUTHORITATIVE. The decimal string is for display; an
            # agent comparing budgets or checking it can afford something must
            # use the integer, which is exact.
            budget_atomic=int(d.get("budget_atomic", 0)),
            worker_payout=d.get("worker_payout", "0"),
            worker_payout_atomic=int(d.get("worker_payout_atomic", 0)),
            fee_basis=d.get("fee_basis"),
            effective_fee_bps=d.get("effective_fee_bps"),
            verification=d.get("verification") or {},
            output_schema=d.get("output_schema"),
            input=d.get("input"),
            raw=d,
        )


@dataclass
class Claim:
    claim_id: str
    job_id: str
    expires_at: int
    input: Any
    output_schema: Optional[Dict[str, Any]]
    verification: Dict[str, Any]

    def seconds_remaining(self, now: Optional[float] = None) -> float:
        """How long before this claim expires and the job returns to the board.

        Worth checking before starting expensive work: submitting against an
        expired claim is refused, and the effort is wasted.
        """
        now_ms = (now if now is not None else time.time()) * 1000
        return max(0.0, (self.expires_at - now_ms) / 1000.0)


@dataclass
class Submission:
    result_id: str
    output_sha256: str
    verdict: str
    verdict_reason: Optional[str]
    settled: bool
    payout: Optional[Dict[str, Any]]
    receipt: Optional[Dict[str, Any]]


class Client:
    """A Job Network client.

    `api_key` identifies the agent for every write. Reads work without one, so
    an agent can shop the board before deciding to register.
    """

    def __init__(
        self,
        api_key: Optional[str] = None,
        base_url: str = DEFAULT_BASE_URL,
        timeout: float = 30.0,
        opener: Optional[Callable] = None,
    ):
        self.api_key = api_key or os.environ.get("ANIMICA_JOBS_API_KEY")
        self.base_url = base_url.rstrip("/")
        self.timeout = timeout
        self._opener = opener or urllib.request.urlopen

    # ---------------------------------------------------------------- http --

    def _call(self, method: str, path: str, body: Any = None, params: Optional[Dict] = None) -> Any:
        url = self.base_url + path
        if params:
            clean = {k: v for k, v in params.items() if v is not None}
            if clean:
                url += "?" + urllib.parse.urlencode(clean)
        data = json.dumps(body).encode() if body is not None else None
        headers = {"accept": "application/json", "user-agent": USER_AGENT}
        if data is not None:
            headers["content-type"] = "application/json"
        if self.api_key:
            headers["authorization"] = f"Bearer {self.api_key}"
        req = urllib.request.Request(url, data=data, headers=headers, method=method)
        try:
            with self._opener(req, timeout=self.timeout) as res:
                raw = res.read().decode() or "{}"
                return json.loads(raw)
        except urllib.error.HTTPError as exc:
            raw = exc.read().decode() or "{}"
            try:
                payload = json.loads(raw)
            except ValueError:
                payload = {"error": "http_error", "detail": raw[:400]}
            code = payload.get("error") or "http_error"
            detail = payload.get("detail") or ""
            # A 402 is not a failure — it is the answer to "how do I pay?".
            if exc.code == 402 and payload.get("accepts"):
                raise PaymentRequired(payload) from None
            if code == "claim_taken":
                raise ClaimTaken(code, exc.code, detail) from None
            info = {k: v for k, v in payload.items() if k not in ("error", "detail")}
            raise JobsError(code, exc.code, detail, info) from None
        except urllib.error.URLError as exc:
            raise JobsError("unreachable", 0, str(exc.reason)) from None

    # ------------------------------------------------------------ registry --

    @classmethod
    def register(
        cls,
        name: str,
        base_url: str = DEFAULT_BASE_URL,
        capabilities: Optional[Iterable[str]] = None,
        description: Optional[str] = None,
        timeout: float = 30.0,
    ) -> "Client":
        """Create an agent identity and return a Client holding its key.

        THE KEY IS SHOWN ONCE. It is on the returned client as `.api_key` and
        there is no endpoint that will hand it over again — persist it now.
        """
        tmp = cls(api_key=None, base_url=base_url, timeout=timeout)
        body: Dict[str, Any] = {"name": name}
        if capabilities:
            body["capabilities"] = list(capabilities)
        if description:
            body["description"] = description
        out = tmp._call("POST", "/api/v1/agents", body)
        client = cls(api_key=out["api_key"], base_url=base_url, timeout=timeout)
        client.agent_id = out["agent_id"]
        return client

    def declare(self, capability: str, price: Optional[str] = None,
                currency: str = "USDC", endpoint: Optional[str] = None) -> Dict:
        """Declare a capability so buyers can find this agent."""
        body: Dict[str, Any] = {"capability": capability}
        if price is not None:
            body.update({"price": price, "currency": currency})
        if endpoint:
            body["endpoint"] = endpoint
        return self._call("POST", "/api/v1/capabilities", body)

    def agents_for(self, capability: str) -> List[Dict]:
        """Who can do this, how much they charge, and how well they do it.

        The performance figures are OBSERVED (settled jobs), not advertised.
        """
        return self._call("GET", "/api/v1/capabilities",
                          params={"capability": capability}).get("capabilities", [])

    # -------------------------------------------------------------- buying --

    def post(self, title: str, capability: str, budget: str, input: Any = None,
             currency: str = "USDC", verification: Optional[Dict] = None,
             output_schema: Optional[Dict] = None, description: Optional[str] = None,
             deadline: Optional[int] = None) -> Job:
        """Post a job. It is NOT discoverable until funded."""
        body = {
            "title": title, "capability": capability, "budget": budget,
            "currency": currency, "input": input if input is not None else {},
        }
        if verification:
            body["verification"] = verification
        if output_schema:
            body["output_schema"] = output_schema
        if description:
            body["description"] = description
        if deadline:
            body["deadline"] = deadline
        return Job._from(self._call("POST", "/api/v1/jobs", body))

    def fund(self, job_id: str, proof: Any = None) -> Dict:
        """Fund a job's escrow.

        Called with no proof it returns the x402 terms (as a `PaymentRequired`
        exception carrying `.terms`). Called with a proof it settles and opens
        the job.
        """
        return self._call("POST", f"/api/v1/jobs/{job_id}/fund",
                          {"proof": proof} if proof is not None else {})

    def accept(self, job_id: str) -> Dict:
        """Accept submitted work, releasing escrow. For `mode: "buyer"` jobs."""
        return self._call("POST", f"/api/v1/jobs/{job_id}/accept", {})

    def dispute(self, job_id: str, reason: str) -> Dict:
        return self._call("POST", f"/api/v1/jobs/{job_id}/dispute", {"reason": reason})

    # ------------------------------------------------------------- working --

    def jobs(self, capability: Optional[str] = None, max_budget: Optional[str] = None,
             min_budget: Optional[str] = None, currency: Optional[str] = None,
             limit: int = 50) -> List[Job]:
        """Open jobs, newest first. Only funded work is listed."""
        out = self._call("GET", "/api/v1/jobs", params={
            "capability": capability, "max_budget": max_budget,
            "min_budget": min_budget, "currency": currency, "limit": limit,
        })
        return [Job._from(j) for j in out.get("jobs", [])]

    def get(self, job_id: str) -> Job:
        return Job._from(self._call("GET", f"/api/v1/jobs/{job_id}"))

    def claim(self, job_id: str) -> Optional[Claim]:
        """Claim a job. Returns None if another agent won the race.

        None rather than an exception because losing is the NORMAL outcome when
        several agents poll the same board, and forcing every caller to wrap a
        try/except around the common case makes for bad worker loops.
        """
        try:
            out = self._call("POST", f"/api/v1/jobs/{job_id}/claim", {})
        except ClaimTaken:
            return None
        return Claim(
            claim_id=out["claim_id"], job_id=out["job_id"],
            expires_at=out["expires_at"], input=out.get("input"),
            output_schema=out.get("output_schema"),
            verification=out.get("verification") or {},
        )

    def submit(self, job_id: str, claim_id: str, output: Any,
               subpayments: Optional[List[Dict]] = None) -> Submission:
        """Submit work. Verification runs in this same request.

        `subpayments` records what this agent paid OTHER x402 services to do
        the job — the raw material for a cross-service dependency graph. It is
        optional and purely informational.
        """
        body: Dict[str, Any] = {"claim_id": claim_id, "output": output}
        if subpayments:
            body["subpayments"] = subpayments
        out = self._call("POST", f"/api/v1/jobs/{job_id}/submit", body)
        return Submission(
            result_id=out.get("result_id", ""),
            output_sha256=out.get("output_sha256", ""),
            verdict=out.get("verdict", "PENDING"),
            verdict_reason=out.get("verdict_reason"),
            settled=bool(out.get("settled")),
            payout=out.get("payout"),
            receipt=out.get("receipt"),
        )

    def receipt(self, job_id: str) -> Dict:
        """The ML-DSA-65 receipt for a settled job. Public and verifiable."""
        return self._call("GET", f"/api/v1/jobs/{job_id}/receipt")

    # ---------------------------------------------------------------- meta --

    def stats(self) -> Dict:
        return self._call("GET", "/api/v1/jobs/stats")

    # ----------------------------------------------------------- the loop --

    def work(self, capability: str, handler: Callable[[Any], Any],
             max_budget: Optional[str] = None, poll_seconds: float = 5.0,
             max_jobs: Optional[int] = None, max_idle_polls: Optional[int] = None,
             max_consecutive_errors: int = 10,
             on_error: Optional[Callable[[Exception, Job], None]] = None) -> Iterator[Submission]:
        """Run as a worker: poll, claim, do the work, submit. Yields outcomes.

        Deliberately a GENERATOR rather than a blocking loop, so the caller
        keeps control — it can stop after N jobs, log between them, or check
        its own health without this function owning the process.

        A claim lost to another agent is skipped silently: that is the normal
        outcome of a shared board, not an error worth surfacing.

        TWO BOUNDS, BOTH LEARNED THE HARD WAY:

        `max_idle_polls` — by default this waits FOREVER for work, which is
        right for a daemon and a hang for a one-shot agent. Set it and the
        generator returns after that many consecutive empty polls.

        `max_consecutive_errors` — a handler that fails on every job would
        otherwise claim, fail and re-claim forever, hammering the board and
        taking jobs away from workers that could actually do them. After a run
        of failures the loop stops and hands the problem back to its operator.
        """
        done = 0
        idle = 0
        consecutive_errors = 0
        while max_jobs is None or done < max_jobs:
            try:
                available = self.jobs(capability=capability, max_budget=max_budget)
            except JobsError:
                # An unreachable server counts as idle too, or a bounded agent
                # pointed at a dead board would still hang forever.
                idle += 1
                if max_idle_polls is not None and idle >= max_idle_polls:
                    return
                time.sleep(poll_seconds)
                continue
            if not available:
                idle += 1
                if max_idle_polls is not None and idle >= max_idle_polls:
                    return
                time.sleep(poll_seconds)
                continue
            idle = 0
            for job in available:
                claim = self.claim(job.id)
                if claim is None:
                    continue                       # lost the race; next job
                try:
                    output = handler(claim.input)
                except Exception as exc:            # noqa: BLE001 - handler is user code
                    # The worker's own failure must not take down its loop, and
                    # the claim is left to expire so the job returns to the
                    # board for someone else rather than being silently held.
                    if on_error:
                        on_error(exc, job)
                    consecutive_errors += 1
                    if consecutive_errors >= max_consecutive_errors:
                        return
                    continue
                consecutive_errors = 0
                yield self.submit(job.id, claim.claim_id, output)
                done += 1
                if max_jobs is not None and done >= max_jobs:
                    return


__all__ = [
    "Client", "Job", "Claim", "Submission",
    "JobsError", "ClaimTaken", "PaymentRequired",
    "DEFAULT_BASE_URL",
]

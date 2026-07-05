from __future__ import annotations

import asyncio
import hashlib
import json
import logging
import time
from collections import deque
from dataclasses import replace
from typing import Awaitable, Callable, Deque, Dict, Optional

from core.utils.pow import micro_threshold_to_target256
from mining.stratum_server import StratumJob, StratumServer

from .config import PoolConfig
from .core import MiningCoreAdapter, MiningJob, freeze_mining_job, job_validation_fingerprint
from .job_manager import JobManager


def _fingerprint_header(header: dict) -> str:
    encoded = json.dumps(header, sort_keys=True, separators=(",", ":"), default=str)
    return hashlib.sha256(encoded.encode("utf-8")).hexdigest()


def _effective_job_id(source_job_id: str, validation_fingerprint: str) -> str:
    source = str(source_job_id or "job").strip() or "job"
    suffix = (validation_fingerprint or "")[:12]
    return f"{source}-{suffix}" if suffix else source


class PoolShareValidator:
    def __init__(
        self,
        adapter: MiningCoreAdapter,
        *,
        pool_mode: str = "pps",
        logger: Optional[logging.Logger] = None,
        is_rejected: Optional[Callable[[str], bool]] = None,
    ) -> None:
        self._adapter = adapter
        self._pool_mode = str(pool_mode or "pps").strip().lower()
        if self._pool_mode not in {"pps", "solo", "both"}:
            self._pool_mode = "pps"
        self._log = logger or logging.getLogger("animica.stratum_pool.validator")
        # Returns True for addresses the pool has rejected on version policy.
        self._is_rejected = is_rejected or (lambda _addr: False)

    async def validate(self, job: StratumJob, submit_params):
        address = str(submit_params.get("_address") or "").strip()
        if not address:
            return False, "missing miner payout address", False, 0
        if not address.startswith("anim1"):
            return False, "invalid miner payout address", False, 0
        if self._is_rejected(address):
            return False, "miner version too old — update required", False, 0
        raw_template = job.raw if isinstance(job.raw, dict) else {}
        source_job_id = str(raw_template.get("_sourceJobId") or job.job_id)
        if raw_template.get("_sourceJobId") != source_job_id:
            raw_template = dict(raw_template)
            raw_template["_sourceJobId"] = source_job_id
        mining_job = MiningJob(
            job_id=job.job_id,
            source_job_id=source_job_id,
            header=job.header,
            theta_micro=job.theta_micro,
            share_target=job.share_target,
            height=submit_params.get("height") or job.height or 0,
            hints=job.hints,
            target=job.target,
            sign_bytes=job.sign_bytes,
            template_id=raw_template.get("templateId"),
            parent_hash=job.parent_hash,
            parent_height=job.parent_height,
            chain_id=job.chain_id,
            expires_at=job.expires_at,
            issued_theta_micro=raw_template.get("_issuedThetaMicro"),
            share_threshold_micro=raw_template.get("_shareThresholdMicro"),
            share_target_int=raw_template.get("_shareTargetInt"),
            block_target_int=raw_template.get("_blockTargetInt"),
            mix_seed=raw_template.get("_mixSeed"),
            proof_type=raw_template.get("_proofType"),
            validation_fingerprint=raw_template.get("_validationFingerprint"),
            raw=raw_template,
        )
        try:
            return await self._adapter.validate_and_submit_share(
                mining_job, submit_params
            )
        except Exception as exc:  # noqa: BLE001
            self._log.warning("share validation failed", exc_info=True)
            return False, str(exc), False, 0


class StratumPoolServer:
    def __init__(
        self,
        adapter: MiningCoreAdapter,
        config: PoolConfig,
        job_manager: JobManager,
        *,
        logger: Optional[logging.Logger] = None,
    ) -> None:
        self._adapter = adapter
        self._config = config
        self._job_manager = job_manager
        self._log = logger or logging.getLogger("animica.stratum_pool.server")
        # Addresses rejected by the pool-level miner-version policy (Phase 5).
        # Populated at authorize time; consulted by the share validator so old
        # miners earn nothing until they update. Reversible via config.
        self._version_rejected: set[str] = set()
        self._validator = PoolShareValidator(
            adapter,
            pool_mode=config.pool_mode,
            logger=logger,
            is_rejected=self._is_version_rejected,
        )
        self._last_published_job_id: Optional[str] = None
        self._last_published_fingerprint: Optional[str] = None
        self._last_diff_tuple: Optional[tuple[float, int]] = None
        self._current_stratum_job: Optional[StratumJob] = None
        self._current_share_threshold_micro: Optional[int] = None
        self._external_submit_hook: Optional[
            Callable[[object, StratumJob, dict, bool, Optional[str], bool, int], Awaitable[None]]
        ] = None
        self._vardiff_samples: Deque[tuple[float, bool, bool]] = deque(maxlen=24)
        self._vardiff_lock = asyncio.Lock()
        self._last_vardiff_adjust_ts = 0.0
        self._vardiff_min_samples = 8
        self._vardiff_cooldown_s = 2.0
        self._server = StratumServer(
            host=config.host,
            port=config.port,
            default_share_target=float(getattr(config, "start_difficulty", 0.01) or 0.01),
            default_theta_micro=0,
            max_cached_jobs=128,
            validator=self._validator,
            submit_hook=self._handle_share_submit,
            on_worker_authorized=self._register_aicf_worker,
            on_aicf_result=self._handle_aicf_result,
            on_xmr_result=self._handle_xmr_result,
            cryptonote_handler=self._handle_cryptonote_takeover,
            pool_mode=config.pool_mode,
            solo_port=config.solo_port,
        )
        # XMR (Monero RandomX) dual-mining infrastructure. Initialised
        # lazily in start() once the pool config has been validated.
        # See python/animica/stratum_pool/xmr.py for the components.
        self._xmr_client = None  # type: ignore[assignment]
        self._xmr_jm = None      # type: ignore[assignment]
        self._xmr_validator = None  # type: ignore[assignment]
        self._xmr_ledger = None  # type: ignore[assignment]
        self._xmr_stratum = None  # type: ignore[assignment]
        self._xmr_enabled = False
        self._aicf_dispatcher_task: Optional[asyncio.Task] = None
        self._aicf_dispatcher_stop = asyncio.Event()
        # AICF jobs the pool has pushed but not yet seen a result for.
        # Maps (job_id, worker_address) → expires_at. The tuple key lets
        # multiple workers race the same job (K-way replication, see
        # rpc/methods/aicf_jobs.py::_REPLICAS_DEFAULT). The pool tracks
        # each (job_id, addr) lease separately so timing out one slow
        # worker doesn't drop bookkeeping for its fellow racers.
        self._aicf_inflight: Dict[tuple[str, str], float] = {}
        self._aicf_poll_interval_s = 2.0

    def _is_version_rejected(self, address: str) -> bool:
        return str(address or "") in self._version_rejected

    def _enforce_version(self, session: object, features: dict) -> bool:
        """Apply the pool-level miner-version policy at authorize time.

        Returns True if the miner is allowed to continue. When enforcement is on
        and the miner is too old (or reports no version), its address is recorded
        as rejected — so the share validator refuses its shares — and AICF
        registration is skipped. Policy only; never affects chain consensus.
        """
        from . import version_gate
        address = str(getattr(session, "address", None)
                      or getattr(session, "worker", None) or "")
        verdict = version_gate.evaluate(
            features, getattr(self._config, "min_miner_version", ""),
            enforce=bool(getattr(self._config, "require_min_version", False)))
        if verdict["ok"]:
            if address:
                self._version_rejected.discard(address)
            return True
        if address:
            self._version_rejected.add(address)
        try:  # best-effort: drop authorization so nothing else routes here
            setattr(session, "authorized", False)
        except Exception:  # noqa: BLE001
            pass
        self._log.warning(
            "rejected miner on version policy: address=%s reported=%r minimum=%s",
            address or "?", verdict["reported"], verdict["minimum"])
        return False

    async def _register_aicf_worker(self, session: object, features: dict) -> None:
        """Auto-register the authorized miner as an AICF worker.

        Miners advertise their served model tiers + hardware via the
        `aicf` key in mining.subscribe features:

            features = {"aicf": {"tiers": ["standard","premium"],
                                  "hardware": {"gpu": "rtx5090", "vram_gb": 24}}}

        A miner that omits `aicf` is registered with an empty tier list,
        which the aicf job dispatcher treats as "PoW-only — do not route
        inference here". This keeps the call non-disruptive for old
        miners while letting model-serving miners opt in just by adding
        the features key.
        """
        # Pool-level version policy: reject old miners before anything routes.
        if not self._enforce_version(session, features):
            return
        if not getattr(session, "authorized", False):
            return
        address = getattr(session, "address", None) or getattr(session, "worker", None)
        if not address:
            return
        aicf_features = (features or {}).get("aicf") or {}
        if not isinstance(aicf_features, dict):
            aicf_features = {}
        tiers = aicf_features.get("tiers") or []
        if not isinstance(tiers, list):
            tiers = []
        hardware = aicf_features.get("hardware") or {}
        if not isinstance(hardware, dict):
            hardware = {}
        try:
            await self._adapter._rpc_call(
                "aicf.workerRegister",
                {"address": str(address), "tiers": list(tiers), "hardware": dict(hardware)},
            )
            self._log.info(
                "aicf-worker registered via stratum: address=%s tiers=%s",
                address,
                tiers,
            )
        except Exception as exc:
            self._log.warning(
                "aicf.workerRegister failed for stratum session: %s", exc
            )

    def set_submit_hook(
        self,
        hook: Optional[
            Callable[[object, StratumJob, dict, bool, Optional[str], bool, int], Awaitable[None]]
        ],
    ) -> None:
        self._external_submit_hook = hook

    def set_rental_resolver(
        self,
        resolver: Optional[Callable[[str, str], Optional[dict]]],
    ) -> None:
        """Wire the rig-rental lookup (metrics.active_rental) so the XMR
        stratum handler can redirect a rented rig's credit to the renter."""
        self._rental_resolver = resolver
        xmr = getattr(self, "_xmr_stratum", None)
        if xmr is not None:
            xmr.set_rental_resolver(resolver)

    def _difficulty_value_to_threshold_micro(self, value: float, theta_micro: int) -> int:
        """
        Normalize configured difficulty values into θµ thresholds.

        Compatibility:
        - value <= 1.0: legacy ratio mode (ratio vs current θ)
        - value > 1.0:  absolute θµ threshold
        """
        numeric = max(float(value or 0.0), 0.0)
        theta = max(int(theta_micro or 0), 0)
        if numeric <= 0:
            return 1
        if numeric <= 1.0:
            if theta <= 0:
                return 1
            return max(1, int(theta * numeric))
        return max(1, int(numeric))

    def _share_bounds_for_theta(self, theta_micro: int) -> tuple[int, int]:
        theta = max(int(theta_micro or 0), 0)
        lower = self._difficulty_value_to_threshold_micro(
            float(self._config.min_difficulty), theta
        )
        upper = self._difficulty_value_to_threshold_micro(
            float(self._config.max_difficulty), theta
        )
        if theta > 0:
            # Share threshold should not become harder than the block threshold.
            lower = min(lower, theta)
            upper = min(upper, theta)
        lower = max(1, int(lower))
        upper = max(1, int(upper))
        if upper < lower:
            upper = lower
        return lower, upper

    @staticmethod
    def _ratio_to_threshold_micro(theta_micro: int, ratio: float) -> int:
        theta = max(int(theta_micro or 0), 0)
        if theta <= 0:
            return 0
        value = max(float(ratio or 0.0), 0.0)
        if value <= 0.0:
            value = 1.0
        return max(1, int(theta * min(value, 1.0)))

    @staticmethod
    def _threshold_micro_to_ratio(theta_micro: int, threshold_micro: int) -> float:
        theta = max(int(theta_micro or 0), 0)
        if theta <= 0:
            return 1.0
        ratio = float(max(1, int(threshold_micro))) / float(theta)
        return min(max(ratio, 1e-9), 1.0)

    @staticmethod
    def _coerce_positive_float(value: object, *, fallback: float) -> float:
        try:
            parsed = float(value)
        except (TypeError, ValueError):
            return fallback
        return parsed if parsed > 0.0 else fallback

    def _resolve_share_target(self, requested: float, theta_micro: int) -> tuple[float, int]:
        lower, upper = self._share_bounds_for_theta(theta_micro)
        requested_threshold = self._ratio_to_threshold_micro(theta_micro, requested)
        if requested_threshold <= 0:
            requested_threshold = lower
        if self._current_share_threshold_micro is not None:
            current_threshold = int(self._current_share_threshold_micro)
        else:
            # Bootstrap the very first target at an achievable start_difficulty so
            # miners submit shares immediately and the vardiff has real samples to
            # tune from — in BOTH directions. Two failure modes this avoids:
            #  * Starting at the template's block-difficulty share (requested ≈ θ)
            #    pins the vardiff at the block target forever: a block-difficulty
            #    share is unsubmittable, so no samples ever arrive to step it down
            #    and miners are credited only when they find a whole block.
            #  * Starting at the min_difficulty floor (the requested default when
            #    the template carries no share target) opens the pool at the
            #    absolute floor and floods until the vardiff climbs. We therefore
            #    ignore the requested/floor default here and anchor at
            #    start_difficulty; per-miner vardiff diverges from there.
            start_threshold = self._ratio_to_threshold_micro(
                theta_micro, float(getattr(self._config, "start_difficulty", 0.01))
            )
            current_threshold = start_threshold if start_threshold > 0 else lower
        clamped_threshold = min(max(current_threshold, lower), upper)
        if clamped_threshold != current_threshold:
            self._log.warning(
                "share_target_clamped",
                extra={
                    "requested_threshold_micro": current_threshold,
                    "clamped_threshold_micro": clamped_threshold,
                    "min_difficulty": self._config.min_difficulty,
                    "max_difficulty": self._config.max_difficulty,
                    "theta_micro": theta_micro,
                },
            )
        if theta_micro > 0 and clamped_threshold >= int(theta_micro * 0.95):
            self._log.warning(
                "share_target_near_block_target",
                extra={
                    "share_threshold_micro": clamped_threshold,
                    "theta_micro": theta_micro,
                    "note": "Shares are close to full block difficulty.",
                },
            )
        ratio = self._threshold_micro_to_ratio(theta_micro, clamped_threshold)
        return ratio, clamped_threshold

    async def start(self) -> None:
        self._job_manager.subscribe(self._on_new_job)
        self._job_manager.start()
        await self._server.start()
        self._aicf_dispatcher_stop.clear()
        self._aicf_dispatcher_task = asyncio.create_task(
            self._aicf_dispatch_loop(), name="aicf_dispatch_loop"
        )
        await self._maybe_start_xmr()

    async def stop(self) -> None:
        self._aicf_dispatcher_stop.set()
        if self._aicf_dispatcher_task is not None:
            try:
                await asyncio.wait_for(self._aicf_dispatcher_task, timeout=5.0)
            except (asyncio.TimeoutError, asyncio.CancelledError):
                self._aicf_dispatcher_task.cancel()
            self._aicf_dispatcher_task = None
        if self._xmr_stratum is not None:
            try:
                await self._xmr_stratum.stop()
            except Exception as exc:
                self._log.warning("xmr cryptonote stratum stop: %s", exc)
            self._xmr_stratum = None
        if self._xmr_jm is not None:
            try:
                await self._xmr_jm.stop()
            except Exception as exc:
                self._log.warning("xmr job manager stop: %s", exc)
            self._xmr_jm = None
        if self._xmr_client is not None:
            try:
                await self._xmr_client.close()
            except Exception:
                pass
            self._xmr_client = None
        if self._xmr_validator is not None:
            try:
                self._xmr_validator.close()
            except Exception:
                pass
            self._xmr_validator = None
        await self._server.stop()
        await self._job_manager.stop()

    async def _maybe_start_xmr(self) -> None:
        """Initialize XMR dual-mining if config + monerod are ready.

        Config keys honored (via env vars; the pool config dataclass
        doesn't currently carry these — easier to keep them env-only
        until the operator opts in):

          ANIMICA_POOL_XMR_ENABLED=1                  # master toggle
          ANIMICA_POOL_XMR_ADDRESS=49Rc...HUTQxrXfJ   # pool fee receive
          ANIMICA_POOL_MONEROD_URL=http://127.0.0.1:18081

        The pool starts even if XMR setup fails — Animica SHA3 mining
        is independent and shouldn't go down because monerod is.
        """
        import os
        if os.environ.get("ANIMICA_POOL_XMR_ENABLED", "").strip() != "1":
            return
        addr = os.environ.get("ANIMICA_POOL_XMR_ADDRESS", "").strip()
        if not addr:
            self._log.warning(
                "ANIMICA_POOL_XMR_ENABLED=1 but ANIMICA_POOL_XMR_ADDRESS empty — "
                "XMR mining disabled. Set it to the pool's Monero primary "
                "address (45/4A/8... prefix)."
            )
            return
        try:
            from animica.stratum_pool.xmr import (
                difficulty_to_target_hex,
                make_default_components,
            )
        except Exception as exc:
            self._log.warning("XMR module unavailable (%s); skipping", exc)
            return
        try:
            client, jm, validator, ledger = await make_default_components(
                pool_xmr_address=addr,
                monerod_url=os.environ.get(
                    "ANIMICA_POOL_MONEROD_URL", "http://127.0.0.1:18081"
                ),
                num_validator_workers=int(
                    os.environ.get("ANIMICA_POOL_XMR_VALIDATOR_WORKERS", "4")
                ),
                share_difficulty=int(
                    os.environ.get("ANIMICA_POOL_XMR_SHARE_DIFF", "10000")
                ),
            )
        except Exception as exc:
            self._log.warning("XMR start failed: %s", exc, exc_info=True)
            return
        self._xmr_client = client
        self._xmr_jm = jm
        self._xmr_validator = validator
        self._xmr_ledger = ledger
        self._xmr_enabled = True

        # Whenever the XMR job manager builds a fresh template, broadcast
        # it to all subscribed miners.
        async def _broadcast(job) -> None:  # type: ignore[no-untyped-def]
            try:
                await self._server.push_xmr_job(
                    job_id=job.job_id,
                    blob_hex=job.blockhashing_blob.hex(),
                    # Proper cryptonote target (top 4 bytes of 2^256/diff, LE).
                    # NOT the difficulty integer as hex — that made xmrig mine
                    # at diff ~2 and every share got rejected low_difficulty.
                    target_hex=difficulty_to_target_hex(job.pool_difficulty),
                    seed_hash_hex=job.seed_hash.hex(),
                    height=job.height,
                )
            except Exception as exc:
                self._log.warning("xmr broadcast failed: %s", exc, exc_info=False)
        jm.subscribe(_broadcast)

        # Cryptonote pool handler. We always build the XmrStratumServer
        # object — it's the share/session bookkeeping used by the
        # single-port protocol-detect handoff. Optionally we ALSO bind
        # a dedicated TCP port (env-gated) for operators who want a
        # separate-port deployment, but the default is single-port
        # (everything on the existing Animica stratum port).
        from animica.stratum_pool.xmr_stratum import XmrStratumServer
        sep_port_env = os.environ.get("ANIMICA_POOL_XMR_PORT", "").strip()
        sep_port = int(sep_port_env) if sep_port_env else 0
        bind_host = os.environ.get("ANIMICA_POOL_XMR_HOST", self._config.host)
        # host/port=0 means "single-port mode" — no separate listener;
        # the cryptonote_handler callback handles takeovers on the
        # Animica port.
        stratum_xmr = XmrStratumServer(
            host=bind_host, port=sep_port or 0,
            job_manager=jm, validator=validator, ledger=ledger,
            rental_resolver=getattr(self, "_rental_resolver", None),
        )
        if sep_port > 0:
            try:
                await stratum_xmr.start()
                self._log.info(
                    "XMR separate-port mode: cryptonote on %s:%d", bind_host, sep_port,
                )
            except Exception as exc:
                self._log.warning(
                    "XMR separate-port listener failed on %s:%d (%s); "
                    "miners can still connect via the Animica port using "
                    "cryptonote pool protocol — protocol detection routes "
                    "them to the same handler.",
                    bind_host, sep_port, exc,
                )
        else:
            # Single-port mode: no separate TCP listener, BUT we must still
            # subscribe the cryptonote broadcaster to the job manager so the
            # sessions adopted via protocol-detect takeover receive new-block
            # `job` notifications. Without this they only ever get the login
            # job; once it ages out of the validator's 5-min cache (a couple
            # of blocks later) every submitted share is rejected
            # `stale_or_unknown_job`. start() would do this subscribe too, but
            # it also binds a listener we don't want here — so subscribe directly.
            jm.subscribe(stratum_xmr._broadcast_job)
            self._log.info(
                "XMR single-port mode: cryptonote login routes through the "
                "Animica stratum port (no separate TCP listener); new-block "
                "jobs pushed to adopted sessions."
            )
        self._xmr_stratum = stratum_xmr

        await jm.start()
        self._log.info(
            "XMR dual-mining enabled; pool fee → %s ; cryptonote mode=%s",
            addr,
            f"sep:{sep_port}" if sep_port > 0 else "single-port (animica port)",
        )

    async def _handle_cryptonote_takeover(
        self, reader, writer, first_msg: dict,
    ) -> None:
        """Single-port handoff. The Animica stratum sniffed a cryptonote
        `login` on the first inbound line and handed the connection here.
        We adopt the same handler logic XmrStratumServer uses, but reuse
        the existing reader/writer (no new TCP accept).
        """
        if not self._xmr_enabled or self._xmr_stratum is None:
            # XMR disabled at this pool — politely reject so the miner
            # can switch ports/pools without confusion.
            try:
                import json as _json
                writer.write((_json.dumps({
                    "id": first_msg.get("id"),
                    "jsonrpc": "2.0",
                    "error": {
                        "code": -1,
                        "message": "this pool isn't running Monero (XMR) mining yet",
                    },
                }) + "\n").encode("utf-8"))
                await writer.drain()
            except Exception:
                pass
            return
        # Re-feed the login message into the cryptonote session handler.
        # XmrStratumServer._handle_client expects to readline() the login
        # itself, so wrap the leftover reader with one more replay layer
        # that re-emits the JSON-serialized first_msg first.
        import json as _json
        from animica.stratum_pool.xmr_stratum import XmrSession
        import secrets as _secrets

        # Capture the XMR server in a local — a concurrent pool shutdown can
        # null self._xmr_stratum mid-handler, and we'd crash on `._lock`.
        xmr = self._xmr_stratum

        # Build a minimal session and dispatch as if the line had just
        # arrived. We bypass the per-connection accept-loop and drive
        # the same _dispatch logic.
        session_id = _secrets.token_hex(8)
        sess = XmrSession(session_id=session_id, writer=writer)
        async with xmr._lock:
            xmr._sessions[session_id] = sess
        self._log.info(
            "xmr session %s adopted from animica port (single-port mode)",
            session_id,
        )
        try:
            # Process the login we already parsed
            await xmr._dispatch(sess, first_msg)
            # Then continue reading from the wire
            while not sess.closed:
                line = await reader.readline()
                if not line:
                    break
                try:
                    msg = _json.loads(line.decode("utf-8").strip())
                except Exception:
                    continue
                await xmr._dispatch(sess, msg)
        except Exception as exc:
            self._log.warning("xmr single-port session %s error: %s",
                              session_id, exc, exc_info=False)
        finally:
            sess.closed = True
            async with xmr._lock:
                xmr._sessions.pop(session_id, None)
            self._log.info(
                "xmr single-port session %s closed (accepted=%d rejected=%d)",
                session_id, sess.shares_accepted, sess.shares_rejected,
            )

    async def _handle_xmr_result(self, session: object, params: dict) -> dict:
        """Called by mining.stratum_server when a miner sends submit_xmr.

        Delegates to XmrShareValidator + XmrShareLedger. Returns
        {accepted, reason, block_solved} for the wire response.
        """
        if not self._xmr_enabled or self._xmr_validator is None:
            return {"accepted": False, "reason": "xmr_not_enabled"}
        miner_id = getattr(session, "address", None) or getattr(
            session, "worker", None
        ) or getattr(session, "session_id", "unknown")
        ok, reason, share = await self._xmr_validator.validate(
            miner_id=str(miner_id),
            job_id=str(params.get("jobId") or params.get("job_id") or ""),
            nonce_hex=str(params.get("nonce") or ""),
            result_hex=str(params.get("result") or ""),
        )
        if not ok or share is None:
            return {"accepted": False, "reason": reason}
        await self._xmr_ledger.record(share)
        if share.block_solved:
            self._log.info(
                "🎉 XMR block solved by miner=%s job=%s height=%s",
                miner_id, share.job_id, getattr(self._xmr_jm.current_job, "height", "?"),
            )
            # Block-finder is responsible for submitting via monerod.
            # We trigger an async submit so the wire response doesn't
            # block on the RPC round-trip.
            try:
                job = self._xmr_jm.current_job
                if job is not None and self._xmr_client is not None:
                    # Splice the winning nonce into the full template blob
                    # so the submit_block payload reflects the solved nonce.
                    # The miner's blob mirrors the pool's blockhashing_blob;
                    # we wrote the nonce into bytes [39:43] for hashing,
                    # but submit_block wants the full blocktemplate_blob —
                    # write the same 4 bytes at the same offset.
                    blob = bytearray(job.blob)
                    nonce_le = share.nonce.to_bytes(4, "little")
                    blob[39:43] = nonce_le
                    asyncio.create_task(
                        self._xmr_client.submit_block(bytes(blob).hex())
                    )
            except Exception as exc:
                self._log.warning("xmr submit_block dispatch failed: %s", exc)
        return {
            "accepted": True,
            "reason": "ok",
            "block_solved": share.block_solved,
        }

    async def _aicf_dispatch_loop(self) -> None:
        """Periodically claim AICF jobs for each connected AICF-capable
        miner and push them via mining.aicf.notify.

        Implementation note: we claim ON BEHALF of the miner using their
        payout address — the node's job queue is per-worker-address, so
        the same address that authorized via stratum is what we send to
        `aicf.workerClaimNextJob`. The job's lease keeps it claimed until
        the miner replies with mining.aicf.submit (which flows back to
        aicf.workerSubmitResult via _handle_aicf_result).
        """
        while not self._aicf_dispatcher_stop.is_set():
            try:
                await self._dispatch_one_round()
            except Exception as exc:
                self._log.warning("aicf dispatcher tick failed: %s", exc)
            try:
                await asyncio.wait_for(
                    self._aicf_dispatcher_stop.wait(),
                    timeout=self._aicf_poll_interval_s,
                )
            except asyncio.TimeoutError:
                continue

    async def _dispatch_one_round(self) -> None:
        sessions = self._server.list_aicf_capable_sessions()
        if not sessions:
            return
        now = time.time()
        # Drop expired in-flight bookkeeping so stale entries don't
        # gate future claims; the node's lease timeout reclaims them.
        expired_keys = [k for k, exp in self._aicf_inflight.items() if exp < now]
        for k in expired_keys:
            self._aicf_inflight.pop(k, None)
        # Sessions whose address is already serving an in-flight job
        # are skipped — one job at a time per miner keeps things simple
        # and prevents over-allocation. With race replication, multiple
        # WORKERS may be racing a single job_id; busy is still per-addr.
        busy = {addr for (_jid, addr) in self._aicf_inflight.keys()}
        for sess in sessions:
            addr = (sess.address or "").strip()
            if not addr or addr in busy:
                continue
            feats = sess.subscribed_features or {}
            aicf_feats = feats.get("aicf") if isinstance(feats, dict) else None
            tiers = list((aicf_feats or {}).get("tiers") or [])
            try:
                claimed = await self._adapter._rpc_call(
                    "aicf.workerClaimNextJob",
                    {"address": addr, "tiers": tiers},
                )
            except Exception as exc:
                self._log.debug("aicf.workerClaimNextJob failed for %s: %s", addr, exc)
                continue
            if not isinstance(claimed, dict) or not claimed.get("job_id"):
                continue
            job_id = str(claimed["job_id"])
            spec = claimed.get("spec") or {}
            tier = str(claimed.get("tier") or "standard")
            cost = claimed.get("estimated_cost_animica")
            expires = float(claimed.get("claim_expires_at") or (now + 60))
            ok = await self._server.push_aicf_job(
                worker_address=addr,
                job_id=job_id,
                spec=spec if isinstance(spec, dict) else {},
                tier=tier,
                claim_expires_at=expires,
                estimated_cost_animica=(
                    float(cost) if isinstance(cost, (int, float)) else None
                ),
            )
            if ok:
                # Cap pool-side "busy" tracking well below the node-side
                # lease (default 10 min). The node's stub fallback fires
                # at ~60s — keeping the worker marked busy for the full
                # lease soft-bans it from new dispatches even after the
                # server has moved on, which manifested as "no AICF worker
                # picked up the job" 3 minutes after a stubbed completion.
                local_expires = min(expires, now + 90.0)
                self._aicf_inflight[(job_id, addr)] = local_expires
                busy.add(addr)

    async def _handle_aicf_result(self, session: object, params: dict) -> dict:
        """Relay a miner's mining.aicf.submit to aicf.workerSubmitResult.
        Returns the dict shipped back to the miner."""
        job_id = str(params.get("jobId") or "")
        text = params.get("text") or ""
        latency_ms = params.get("latencyMs") or 0
        address = (getattr(session, "address", None) or "").strip()
        if not job_id or not address:
            return {"accepted": False, "reason": "missing_jobId_or_address"}
        try:
            r = await self._adapter._rpc_call(
                "aicf.workerSubmitResult",
                {
                    "address": address,
                    "job_id": job_id,
                    "text": str(text),
                    "latency_ms": int(latency_ms) if isinstance(latency_ms, (int, float)) else 0,
                },
            )
        except Exception as exc:
            self._log.warning(
                "aicf.workerSubmitResult relay failed job=%s: %s", job_id, exc
            )
            return {"accepted": False, "reason": str(exc)}
        # Clear the in-flight slot for this (job_id, addr). On race
        # replication a winner submit also implicitly releases other
        # workers' lingering bookkeeping for the same job_id: the node
        # has the authoritative terminal state, so we drop every key
        # matching this job_id to free fellow racers for new dispatch.
        if isinstance(r, dict) and (r.get("accepted") or r.get("state") in {"completed", "failed"}):
            keys_for_job = [k for k in self._aicf_inflight if k[0] == job_id]
            for k in keys_for_job:
                self._aicf_inflight.pop(k, None)
        else:
            self._aicf_inflight.pop((job_id, address), None)
        if not isinstance(r, dict):
            return {"accepted": False, "reason": "unexpected_response"}
        return {
            "accepted": bool(r.get("accepted")),
            "state": r.get("state"),
            "reason": r.get("reason"),
            "winner_address": r.get("winner_address"),
        }

    async def _on_new_job(self, job: MiningJob) -> None:
        frozen_job = freeze_mining_job(job, fallback_chain_id=self._config.chain_id)
        issued_theta_micro = int(frozen_job.issued_theta_micro or frozen_job.theta_micro or 0)
        raw_hint = dict(frozen_job.raw) if isinstance(frozen_job.raw, dict) else {}
        requested_share_target = float(
            frozen_job.share_target or self._config.min_difficulty
        )
        share_target_provided = raw_hint.get("_shareTargetProvided")
        if share_target_provided is False:
            requested_share_target = float(self._config.min_difficulty)
        elif share_target_provided is True:
            requested_share_target = self._coerce_positive_float(
                raw_hint.get("_requestedShareTarget"),
                fallback=requested_share_target,
            )
        share_target, share_threshold_micro = self._resolve_share_target(
            requested_share_target,
            issued_theta_micro,
        )
        self._current_share_threshold_micro = share_threshold_micro
        if (
            share_target != float(frozen_job.share_target)
            or int(frozen_job.share_threshold_micro or 0) != int(share_threshold_micro)
        ):
            frozen_job = freeze_mining_job(
                replace(
                    frozen_job,
                    share_target=float(share_target),
                    share_threshold_micro=None,
                    share_target_int=None,
                    validation_fingerprint=None,
                ),
                fallback_chain_id=self._config.chain_id,
            )
            self._current_share_threshold_micro = int(frozen_job.share_threshold_micro or 0)

        validation_fingerprint = (
            frozen_job.validation_fingerprint or job_validation_fingerprint(frozen_job)
        )
        effective_job_id = _effective_job_id(
            str(frozen_job.job_id), validation_fingerprint
        )

        header = dict(frozen_job.header or {})
        if frozen_job.sign_bytes:
            header.setdefault("signBytes", frozen_job.sign_bytes)
        if frozen_job.target:
            header.setdefault("target", frozen_job.target)
        if frozen_job.height:
            header.setdefault("number", frozen_job.height)
        if issued_theta_micro > 0:
            header.setdefault("thetaMicro", issued_theta_micro)
            header.setdefault("thetaTargetMicro", issued_theta_micro)
            header.setdefault("theta_target_micro", issued_theta_micro)

        raw_template = dict(frozen_job.raw) if isinstance(frozen_job.raw, dict) else {}
        raw_template["header"] = dict(header)
        raw_template["_sourceJobId"] = str(frozen_job.source_job_id or frozen_job.job_id)
        raw_template["_effectiveJobId"] = effective_job_id
        raw_template["_validationFingerprint"] = validation_fingerprint
        raw_template["_issuedThetaMicro"] = issued_theta_micro
        raw_template["_shareThresholdMicro"] = int(frozen_job.share_threshold_micro or 0)
        raw_template["_shareTargetInt"] = int(frozen_job.share_target_int or 0)
        raw_template["_blockTargetInt"] = int(frozen_job.block_target_int or 0)
        raw_template["_mixSeed"] = frozen_job.mix_seed
        raw_template["_proofType"] = frozen_job.proof_type
        raw_template["_headerFingerprint"] = _fingerprint_header(header)

        stratum_job = StratumJob(
            job_id=effective_job_id,
            header=header,
            share_target=share_target,
            theta_micro=issued_theta_micro,
            hints=frozen_job.hints,
            target=frozen_job.target,
            sign_bytes=frozen_job.sign_bytes or header.get("signBytes"),
            height=frozen_job.height,
            parent_hash=frozen_job.parent_hash,
            parent_height=frozen_job.parent_height,
            chain_id=frozen_job.chain_id,
            expires_at=frozen_job.expires_at,
            proof_type=frozen_job.proof_type,
            raw=raw_template,
        )
        self._current_stratum_job = stratum_job
        diff_tuple = (float(stratum_job.share_target), int(stratum_job.theta_micro))
        if self._last_diff_tuple != diff_tuple:
            await self._server.set_global_difficulty(
                stratum_job.share_target,
                stratum_job.theta_micro,
            )
            self._last_diff_tuple = diff_tuple

        clean_jobs = (
            stratum_job.job_id != self._last_published_job_id
            or validation_fingerprint != self._last_published_fingerprint
        )
        await self._server.publish_job(stratum_job, clean_jobs=clean_jobs)
        self._last_published_job_id = stratum_job.job_id
        self._last_published_fingerprint = validation_fingerprint

    async def _handle_share_submit(
        self,
        session: object,
        job: StratumJob,
        submit_params: dict,
        ok: bool,
        reason: Optional[str],
        is_block: bool,
        tx_count: int,
    ) -> None:
        try:
            await self._maybe_auto_adjust_share_target(job, ok=ok, reason=reason)
        finally:
            if self._external_submit_hook is not None:
                await self._external_submit_hook(
                    session, job, submit_params, ok, reason, is_block, tx_count
                )

    async def _maybe_auto_adjust_share_target(
        self,
        job: StratumJob,
        *,
        ok: bool,
        reason: Optional[str],
    ) -> None:
        theta_micro = int(job.theta_micro or 0)
        if theta_micro <= 0:
            return
        is_low_diff_reject = (not ok) and str(reason or "").strip().lower() == "low difficulty share"

        now = time.time()
        self._vardiff_samples.append((now, bool(ok), bool(is_low_diff_reject)))
        if len(self._vardiff_samples) < self._vardiff_min_samples:
            return
        if now - self._last_vardiff_adjust_ts < self._vardiff_cooldown_s:
            return

        async with self._vardiff_lock:
            if now - self._last_vardiff_adjust_ts < self._vardiff_cooldown_s:
                return
            window = list(self._vardiff_samples)
            total = len(window)
            accepted = sum(1 for _, accepted_flag, _ in window if accepted_flag)
            low_rejects = sum(1 for _, _, low_reject_flag in window if low_reject_flag)

            current_threshold = int(
                self._current_share_threshold_micro
                or self._ratio_to_threshold_micro(theta_micro, job.share_target)
                or 1
            )
            proposed_threshold = current_threshold
            trigger: Optional[str] = None
            if low_rejects / float(total) >= 0.55:
                proposed_threshold = max(1, int(current_threshold * 0.60))
                trigger = "low_difficulty_rejects"
            elif accepted / float(total) >= 0.95 and low_rejects <= 1:
                proposed_threshold = max(1, int(current_threshold * 1.05))
                trigger = "high_accept_rate"
            else:
                return

            lower, upper = self._share_bounds_for_theta(theta_micro)
            proposed_threshold = min(max(proposed_threshold, lower), upper)
            if proposed_threshold == current_threshold:
                return

            new_share_target = self._threshold_micro_to_ratio(theta_micro, proposed_threshold)
            self._current_share_threshold_micro = proposed_threshold
            self._last_vardiff_adjust_ts = now
            self._vardiff_samples.clear()

            raw_template = (
                dict(self._current_stratum_job.raw)
                if self._current_stratum_job is not None
                and isinstance(self._current_stratum_job.raw, dict)
                else {}
            )
            raw_template["_issuedThetaMicro"] = theta_micro
            raw_template["_shareThresholdMicro"] = int(proposed_threshold)
            raw_template["_shareTargetInt"] = int(
                micro_threshold_to_target256(int(proposed_threshold))
            )
            if self._current_stratum_job is not None:
                self._current_stratum_job = replace(
                    self._current_stratum_job,
                    share_target=float(new_share_target),
                    theta_micro=theta_micro,
                    raw=raw_template,
                )

            diff_tuple = (float(new_share_target), int(theta_micro))
            if self._last_diff_tuple != diff_tuple:
                await self._server.set_global_difficulty(new_share_target, theta_micro)
                self._last_diff_tuple = diff_tuple
            if self._current_stratum_job is not None:
                await self._server.publish_job(self._current_stratum_job, clean_jobs=False)

            self._log.info(
                "share_target_auto_adjusted",
                extra={
                    "trigger": trigger,
                    "share_threshold_micro": proposed_threshold,
                    "share_target": new_share_target,
                    "theta_micro": theta_micro,
                    "accepted_samples": accepted,
                    "low_difficulty_reject_samples": low_rejects,
                    "window_size": total,
                },
            )

    async def wait_closed(self) -> None:
        while True:
            await asyncio.sleep(1)

    @property
    def stratum(self) -> StratumServer:
        return self._server

    def stats(self) -> dict:
        return self._server.stats()

    def session_snapshots(self):
        return self._server.session_snapshots()

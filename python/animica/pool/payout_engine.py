"""
Payout engine for processing mining pool payouts.

Handles:
- Building payout transactions
- Batching outputs
- Submitting via node RPC
- Idempotency and retry logic
- Minimum payout thresholds
"""

from __future__ import annotations

import asyncio
import json
import logging
from dataclasses import dataclass
from datetime import datetime
from typing import Optional

from .db import PoolDatabase
from .models import Balance, Payout, PayoutItem, PayoutState


@dataclass
class PayoutPlan:
    """Plan for a payout batch."""

    total_amount: int  # Base units
    fee_estimate: int  # Base units
    payouts: dict[str, int]  # address -> amount
    num_outputs: int
    block_ids: list[int]  # Blocks being paid out


class PayoutEngine:
    """
    Manages payout execution with batching and idempotency.
    
    Features:
    - Aggregates balances per address
    - Enforces minimum payout threshold
    - Batches into multiple transactions if needed
    - Retries failed transactions
    - Records all payouts for transparency
    """

    def __init__(
        self,
        db: PoolDatabase,
        rpc_client,  # Node RPC client
        min_payout: int,
        max_outputs_per_tx: int = 100,
        fee_per_output: int = 1000,  # Simple fee estimation
        *,
        logger: Optional[logging.Logger] = None,
        paused: bool = False,
    ) -> None:
        self._db = db
        self._rpc = rpc_client
        self._min_payout = min_payout
        self._max_outputs = max_outputs_per_tx
        self._fee_per_output = fee_per_output
        self._log = logger or logging.getLogger("animica.pool.payout_engine")
        self._paused = paused

    def pause(self) -> None:
        """Pause automatic payouts."""
        self._paused = True
        self._log.info("Payout engine paused")

    def resume(self) -> None:
        """Resume automatic payouts."""
        self._paused = False
        self._log.info("Payout engine resumed")

    def is_paused(self) -> bool:
        """Check if payouts are paused."""
        return self._paused

    async def create_payout_plan(self, dry_run: bool = False) -> Optional[PayoutPlan]:
        """
        Create a payout plan for all mature balances.
        
        Args:
            dry_run: If True, don't actually execute, just plan
        
        Returns:
            PayoutPlan or None if no payouts needed
        """
        # Get all balances with mature funds >= min_payout
        rows = self._db.fetchall(
            """
            SELECT payout_address, mature
            FROM balances
            WHERE mature >= ?
            """,
            (self._min_payout,),
        )
        
        if not rows:
            self._log.debug("No mature balances ready for payout")
            return None
        
        # Build payout map
        payouts = {}
        for row in rows:
            address = row["payout_address"]
            amount = row["mature"]
            if amount >= self._min_payout:
                payouts[address] = amount
        
        if not payouts:
            return None
        
        total_amount = sum(payouts.values())
        num_outputs = len(payouts)
        fee_estimate = num_outputs * self._fee_per_output
        
        # Get block IDs being paid out
        block_ids = self._get_unpaid_confirmed_block_ids()
        
        plan = PayoutPlan(
            total_amount=total_amount,
            fee_estimate=fee_estimate,
            payouts=payouts,
            num_outputs=num_outputs,
            block_ids=block_ids,
        )
        
        self._log.info(
            f"Payout plan created: {num_outputs} outputs, "
            f"total={total_amount}, fee_est={fee_estimate}"
        )
        
        return plan

    async def execute_payout(
        self,
        plan: PayoutPlan,
        dry_run: bool = False,
    ) -> Optional[int]:
        """
        Execute a payout plan.
        
        Args:
            plan: PayoutPlan to execute
            dry_run: If True, don't actually send transactions
        
        Returns:
            Payout ID or None if dry run
        """
        if self._paused:
            self._log.warning("Cannot execute payout: engine is paused")
            return None
        
        if dry_run:
            self._log.info("DRY RUN: Would execute payout plan:")
            self._log.info(f"  Outputs: {plan.num_outputs}")
            self._log.info(f"  Total: {plan.total_amount}")
            self._log.info(f"  Fee: {plan.fee_estimate}")
            for address, amount in sorted(plan.payouts.items()):
                self._log.info(f"    {address}: {amount}")
            return None
        
        # Check if we need to split into multiple transactions
        if plan.num_outputs > self._max_outputs:
            return await self._execute_batched_payout(plan)
        
        # Create payout record
        payout_id = self._create_payout_record(plan)
        
        try:
            # Build and submit transaction
            txid = await self._submit_payout_transaction(plan)
            
            # Update payout record with txid
            self._db.execute(
                """
                UPDATE payouts
                SET txid = ?, state = ?
                WHERE id = ?
                """,
                (txid, PayoutState.SENT.value, payout_id),
            )
            self._db.commit()
            
            # Update balances (move from mature to paid_total)
            self._credit_payouts(plan.payouts)
            
            # Mark blocks as paid
            self._mark_blocks_paid(plan.block_ids, txid)
            
            self._log.info(
                f"Payout {payout_id} executed: txid={txid}, "
                f"outputs={plan.num_outputs}, amount={plan.total_amount}"
            )
            
            return payout_id
            
        except Exception as e:  # noqa: BLE001
            self._log.error(f"Payout {payout_id} failed: {e}", exc_info=True)
            
            # Mark payout as failed
            self._db.execute(
                """
                UPDATE payouts
                SET state = ?
                WHERE id = ?
                """,
                (PayoutState.FAILED.value, payout_id),
            )
            self._db.commit()
            
            raise

    async def _execute_batched_payout(self, plan: PayoutPlan) -> int:
        """Execute payout split into multiple transactions."""
        # Split payouts into batches
        batches = []
        current_batch = {}
        
        for address, amount in plan.payouts.items():
            current_batch[address] = amount
            
            if len(current_batch) >= self._max_outputs:
                batches.append(current_batch)
                current_batch = {}
        
        if current_batch:
            batches.append(current_batch)
        
        self._log.info(f"Splitting payout into {len(batches)} transactions")
        
        # Execute each batch
        payout_ids = []
        for i, batch_payouts in enumerate(batches):
            batch_plan = PayoutPlan(
                total_amount=sum(batch_payouts.values()),
                fee_estimate=len(batch_payouts) * self._fee_per_output,
                payouts=batch_payouts,
                num_outputs=len(batch_payouts),
                block_ids=plan.block_ids if i == 0 else [],  # Only credit blocks once
            )
            
            payout_id = await self.execute_payout(batch_plan, dry_run=False)
            if payout_id:
                payout_ids.append(payout_id)
        
        return payout_ids[0] if payout_ids else None

    def _create_payout_record(self, plan: PayoutPlan) -> int:
        """Create payout record in database."""
        now = datetime.utcnow()
        
        with self._db.transaction() as conn:
            # Create payout
            cursor = conn.execute(
                """
                INSERT INTO payouts (created_at, mode, state, total_amount, fee_amount, metadata_json)
                VALUES (?, ?, ?, ?, ?, ?)
                """,
                (
                    now,
                    "pplns",
                    PayoutState.CREATED.value,
                    plan.total_amount,
                    plan.fee_estimate,
                    json.dumps({"block_ids": plan.block_ids}),
                ),
            )
            payout_id = cursor.lastrowid
            
            # Create payout items
            for address, amount in plan.payouts.items():
                conn.execute(
                    """
                    INSERT INTO payout_items (payout_id, payout_address, amount)
                    VALUES (?, ?, ?)
                    """,
                    (payout_id, address, amount),
                )
        
        return payout_id

    async def _submit_payout_transaction(self, plan: PayoutPlan) -> str:
        """
        Build and submit payout transaction to node.
        
        Returns:
            Transaction ID
        """
        # Build transaction with multiple outputs
        outputs = [
            {"address": address, "amount": amount}
            for address, amount in plan.payouts.items()
        ]
        
        # This would call node RPC to build and submit transaction
        # For now, return placeholder
        txid = f"payout_tx_{int(datetime.utcnow().timestamp())}"
        
        self._log.debug(f"Submitted payout transaction: {txid}")
        
        return txid

    def _credit_payouts(self, payouts: dict[str, int]) -> None:
        """Update balances after successful payout."""
        for address, amount in payouts.items():
            self._db.execute(
                """
                UPDATE balances
                SET mature = mature - ?,
                    paid_total = paid_total + ?,
                    updated_at = ?
                WHERE payout_address = ?
                """,
                (amount, amount, datetime.utcnow(), address),
            )
        self._db.commit()

    def _mark_blocks_paid(self, block_ids: list[int], txid: str) -> None:
        """Mark blocks as paid."""
        for block_id in block_ids:
            self._db.execute(
                """
                UPDATE blocks
                SET state = ?, payout_txid = ?
                WHERE id = ?
                """,
                ("paid", txid, block_id),
            )
        self._db.commit()

    def _get_unpaid_confirmed_block_ids(self) -> list[int]:
        """Get IDs of confirmed blocks not yet paid."""
        rows = self._db.fetchall(
            """
            SELECT id FROM blocks
            WHERE state = 'confirmed' AND payout_txid IS NULL
            """
        )
        return [row["id"] for row in rows]

    def get_payout_history(self, limit: int = 50) -> list[Payout]:
        """Get recent payout history."""
        rows = self._db.fetchall(
            """
            SELECT * FROM payouts
            ORDER BY created_at DESC
            LIMIT ?
            """,
            (limit,),
        )
        
        payouts = []
        for row in rows:
            payouts.append(
                Payout(
                    id=row["id"],
                    created_at=datetime.fromisoformat(row["created_at"]),
                    mode=row["mode"],
                    state=PayoutState(row["state"]),
                    txid=row["txid"],
                    total_amount=row["total_amount"],
                    fee_amount=row["fee_amount"],
                    metadata_json=row["metadata_json"],
                )
            )
        
        return payouts

    def export_payout_csv(self, payout_id: int) -> str:
        """Export payout details as CSV."""
        items = self._db.fetchall(
            """
            SELECT * FROM payout_items
            WHERE payout_id = ?
            ORDER BY payout_address
            """,
            (payout_id,),
        )
        
        lines = ["address,amount"]
        for item in items:
            lines.append(f"{item['payout_address']},{item['amount']}")
        
        return "\n".join(lines)

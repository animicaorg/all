"""
Block tracking and confirmation monitoring for the mining pool.

Tracks found blocks through their lifecycle:
FOUND -> SUBMITTED -> ACCEPTED -> CONFIRMED -> PAID

Monitors for orphaned blocks and updates balances accordingly.
"""

from __future__ import annotations

import asyncio
import logging
from datetime import datetime
from typing import Optional

from .db import PoolDatabase
from .models import Block, BlockState


class BlockTracker:
    """
    Tracks found blocks and monitors confirmations.
    
    Responsibilities:
    - Record found blocks
    - Monitor block confirmations via RPC
    - Detect orphaned blocks (reorg detection)
    - Update block states
    - Trigger payout calculations when blocks mature
    """

    def __init__(
        self,
        db: PoolDatabase,
        rpc_client,  # Node RPC client
        maturity_blocks: int = 20,
        poll_interval_sec: float = 30.0,
        *,
        logger: Optional[logging.Logger] = None,
    ) -> None:
        self._db = db
        self._rpc = rpc_client
        self._maturity_blocks = maturity_blocks
        self._poll_interval = poll_interval_sec
        self._log = logger or logging.getLogger("animica.pool.block_tracker")
        
        self._running = False
        self._poll_task: Optional[asyncio.Task] = None

    async def start(self) -> None:
        """Start the block tracker polling loop."""
        if self._running:
            return
        
        self._running = True
        self._poll_task = asyncio.create_task(self._poll_loop())
        self._log.info("Block tracker started")

    async def stop(self) -> None:
        """Stop the block tracker."""
        self._running = False
        if self._poll_task:
            self._poll_task.cancel()
            try:
                await self._poll_task
            except asyncio.CancelledError:
                pass
        self._log.info("Block tracker stopped")

    async def record_found_block(
        self,
        height: int,
        block_hash: str,
        prev_hash: str,
        finder_miner_id: str,  # UUID as string
        network_difficulty: float,
        target: str,
        coinbase_value: int,
        finding_share_id: int,
    ) -> int:
        """
        Record a newly found block.
        
        Returns:
            Block ID
        """
        now = datetime.utcnow()
        
        with self._db.transaction() as conn:
            cursor = conn.execute(
                """
                INSERT INTO blocks 
                (height, hash, prev_hash, found_at, finder_miner_id, state, 
                 network_difficulty, target, coinbase_value, confirmations, orphaned)
                VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
                """,
                (
                    height,
                    block_hash,
                    prev_hash,
                    now,
                    finder_miner_id,
                    BlockState.SUBMITTED.value,
                    network_difficulty,
                    target,
                    coinbase_value,
                    0,
                    0,  # False
                ),
            )
            block_id = cursor.lastrowid
        
        self._log.info(
            f"Recorded found block {block_id}: height={height}, hash={block_hash[:16]}..."
        )
        
        return block_id

    async def _poll_loop(self) -> None:
        """Main polling loop for block confirmation monitoring."""
        while self._running:
            try:
                await self._check_pending_blocks()
            except Exception as e:  # noqa: BLE001
                self._log.error(f"Error in block tracker poll loop: {e}", exc_info=True)
            
            await asyncio.sleep(self._poll_interval)

    async def _check_pending_blocks(self) -> None:
        """Check all pending blocks for confirmations and orphans."""
        # Get blocks that need checking (not PAID or ORPHANED)
        blocks = self._db.fetchall(
            """
            SELECT * FROM blocks
            WHERE state NOT IN (?, ?)
            ORDER BY height DESC
            LIMIT 100
            """,
            (BlockState.PAID.value, BlockState.ORPHANED.value),
        )
        
        if not blocks:
            return
        
        self._log.debug(f"Checking {len(blocks)} pending blocks")
        
        for block_row in blocks:
            try:
                await self._check_block(block_row)
            except Exception as e:  # noqa: BLE001
                self._log.error(
                    f"Error checking block {block_row['id']}: {e}",
                    exc_info=True,
                )

    async def _check_block(self, block_row) -> None:
        """Check a single block's status."""
        block_id = block_row["id"]
        block_height = block_row["height"]
        block_hash = block_row["hash"]
        current_state = BlockState(block_row["state"])
        
        # Query node for block info
        try:
            # Try to get block by hash
            block_info = await self._get_block_by_hash(block_hash)
            
            if block_info is None:
                # Block not found - might be orphaned
                if current_state != BlockState.SUBMITTED:
                    # Was previously accepted, now missing - orphaned
                    self._log.warning(
                        f"Block {block_id} (height {block_height}) appears orphaned"
                    )
                    await self._mark_orphaned(block_id)
                return
            
            # Block exists - check if it's in main chain
            canonical_hash = await self._get_block_hash_at_height(block_height)
            
            if canonical_hash != block_hash:
                # Not in main chain - orphaned
                if current_state != BlockState.SUBMITTED:
                    self._log.warning(
                        f"Block {block_id} (height {block_height}) reorged out of main chain"
                    )
                await self._mark_orphaned(block_id)
                return
            
            # Block is in main chain - update confirmations
            chain_head_height = await self._get_chain_head_height()
            confirmations = max(0, chain_head_height - block_height + 1)
            
            # Update block state based on confirmations
            new_state = current_state
            
            if current_state == BlockState.SUBMITTED:
                new_state = BlockState.ACCEPTED
                self._log.info(f"Block {block_id} accepted in main chain")
            
            if confirmations >= self._maturity_blocks and current_state != BlockState.CONFIRMED:
                new_state = BlockState.CONFIRMED
                self._log.info(
                    f"Block {block_id} reached maturity ({confirmations} confirmations)"
                )
            
            # Update database
            self._db.execute(
                """
                UPDATE blocks
                SET confirmations = ?, state = ?
                WHERE id = ?
                """,
                (confirmations, new_state.value, block_id),
            )
            self._db.commit()
            
        except Exception as e:  # noqa: BLE001
            self._log.error(f"Error querying block {block_hash}: {e}")

    async def _mark_orphaned(self, block_id: int) -> None:
        """Mark a block as orphaned and reverse any immature balances."""
        self._db.execute(
            """
            UPDATE blocks
            SET state = ?, orphaned = 1
            WHERE id = ?
            """,
            (BlockState.ORPHANED.value, block_id),
        )
        self._db.commit()
        
        self._log.info(f"Marked block {block_id} as orphaned")
        
        # TODO: Reverse any credited immature balances for this block

    async def _get_block_by_hash(self, block_hash: str) -> Optional[dict]:
        """
        Get block info from node by hash.
        
        TODO: Implement RPC call to node: chain.getBlockByHash(hash)
        Expected response: {"height": int, "hash": str, "confirmations": int, ...}
        """
        try:
            # Placeholder - requires node RPC integration
            return None
        except Exception:  # noqa: BLE001
            return None

    async def _get_block_hash_at_height(self, height: int) -> Optional[str]:
        """
        Get canonical block hash at given height.
        
        TODO: Implement RPC call to node: chain.getBlockByHeight(height)
        Expected response: {"hash": str, ...}
        """
        try:
            # Placeholder - requires node RPC integration
            return None
        except Exception:  # noqa: BLE001
            return None

    async def _get_chain_head_height(self) -> int:
        """
        Get current chain head height.
        
        TODO: Implement RPC call to node: chain.getHead()
        Expected response: {"height": int, ...}
        """
        try:
            # Placeholder - requires node RPC integration
            return 0
        except Exception:  # noqa: BLE001
            return 0

    def get_block_by_id(self, block_id: int) -> Optional[Block]:
        """Get block record by ID."""
        row = self._db.fetchone("SELECT * FROM blocks WHERE id = ?", (block_id,))
        
        if not row:
            return None
        
        return Block(
            id=row["id"],
            height=row["height"],
            hash=row["hash"],
            prev_hash=row["prev_hash"],
            found_at=datetime.fromisoformat(row["found_at"]),
            finder_miner_id=row["finder_miner_id"],
            state=BlockState(row["state"]),
            network_difficulty=row["network_difficulty"],
            target=row["target"],
            coinbase_value=row["coinbase_value"],
            confirmations=row["confirmations"],
            orphaned=bool(row["orphaned"]),
            payout_txid=row["payout_txid"],
            pplns_window_start_share_id=row["pplns_window_start_share_id"],
            pplns_window_end_share_id=row["pplns_window_end_share_id"],
            metadata_json=row["metadata_json"],
        )

    def get_confirmed_unpaid_blocks(self) -> list[Block]:
        """Get blocks that are confirmed but not yet paid."""
        rows = self._db.fetchall(
            """
            SELECT * FROM blocks
            WHERE state = ? AND payout_txid IS NULL
            ORDER BY height ASC
            """,
            (BlockState.CONFIRMED.value,),
        )
        
        blocks = []
        for row in rows:
            blocks.append(
                Block(
                    id=row["id"],
                    height=row["height"],
                    hash=row["hash"],
                    prev_hash=row["prev_hash"],
                    found_at=datetime.fromisoformat(row["found_at"]),
                    finder_miner_id=row["finder_miner_id"],
                    state=BlockState(row["state"]),
                    network_difficulty=row["network_difficulty"],
                    target=row["target"],
                    coinbase_value=row["coinbase_value"],
                    confirmations=row["confirmations"],
                    orphaned=bool(row["orphaned"]),
                    payout_txid=row["payout_txid"],
                    pplns_window_start_share_id=row["pplns_window_start_share_id"],
                    pplns_window_end_share_id=row["pplns_window_end_share_id"],
                    metadata_json=row["metadata_json"],
                )
            )
        
        return blocks

"""
API/RPC introspection for P2P2.

Provides endpoints for node status, peer list, and debug info.
"""

from __future__ import annotations

from typing import Dict, List


class P2PAPI:
    """
    API for P2P introspection.
    
    Exposes methods for RPC/CLI to query P2P status.
    """
    
    def __init__(self, peer_manager, sync_manager, gossip_engine, metrics):
        self.peer_manager = peer_manager
        self.sync_manager = sync_manager
        self.gossip_engine = gossip_engine
        self.metrics = metrics
    
    def get_peer_list(self) -> List[Dict]:
        """
        Get list of connected peers.
        
        Returns peer info suitable for 'animica peer list' command.
        """
        peers = self.peer_manager.get_peer_info()
        
        # Format for display
        result = []
        for peer in peers:
            result.append({
                "id": peer["peer_id"][:16],  # Truncated ID
                "addr": peer["addr"],
                "dir": peer["direction"],
                "state": peer["state"],
                "score": round(peer["score"], 2),
                "rtt_ms": round(peer["rtt_ms"], 1) if peer["rtt_ms"] else None,
                "inflight_blocks": peer["inflight_blocks"],
                "connected_at": peer["connected_at"],
                "last_recv": peer["last_recv"],
            })
        
        return result
    
    def get_peer_debug(self) -> Dict:
        """
        Get detailed debug info for 'animica peer debug'.
        """
        peers = self.peer_manager.get_peer_info()
        sync_status = self.sync_manager.get_status()
        
        return {
            "peers": peers,
            "sync": sync_status,
            "gossip": {
                "seen_blocks": self.gossip_engine.seen_blocks.cache.__len__(),
                "seen_txs": self.gossip_engine.seen_txs.cache.__len__(),
                "inflight_blocks": len(self.gossip_engine.inflight_blocks),
                "inflight_txs": len(self.gossip_engine.inflight_txs),
            },
            "banned": len(self.peer_manager.banned),
        }
    
    def get_node_status(self) -> Dict:
        """
        Get node status for 'animica node status'.
        """
        sync_status = self.sync_manager.get_status()
        peers = self.peer_manager.get_connected_peers()
        
        return {
            "p2p": {
                "version": "2.0.0",
                "listen_addr": self.peer_manager.transport.listen_addr,
                "peers": {
                    "connected": len(peers),
                    "inbound": sum(1 for p in peers if p.direction == "inbound"),
                    "outbound": sum(1 for p in peers if p.direction == "outbound"),
                    "max_inbound": self.peer_manager.config.max_inbound,
                    "max_outbound": self.peer_manager.config.max_outbound,
                },
                "banned": len(self.peer_manager.banned),
            },
            "sync": {
                "is_syncing": sync_status["is_syncing"],
                "target_height": sync_status["target_height"],
                "orphan_pool_size": sync_status["orphan_pool_size"],
                "inflight_blocks": sync_status["inflight_blocks"],
                "stats": {
                    "headers": sync_status["headers_stats"],
                    "blocks": sync_status["blocks_stats"],
                },
            },
            "metrics": self.metrics.to_dict(),
        }

from __future__ import annotations

import os

from p2p import deps


def test_genesis_mismatch_guidance_for_docker_mount(monkeypatch: object) -> None:
    monkeypatch.setenv("ANIMICA_NETWORK", "mainnet")
    monkeypatch.setenv("ANIMICA_COMPOSE_FILE", "/app/ops/docker/docker-compose.mainnet.yml")
    monkeypatch.setenv("ANIMICA_GENESIS_TAG", "deadbeef")

    guidance = deps._format_genesis_reset_guidance("/data/chain-1/animica.db", 1)

    assert "animica node down --volumes" in guidance
    assert "docker compose -f /app/ops/docker/docker-compose.mainnet.yml down -v --remove-orphans" in guidance
    assert "docker volume rm animica_mainnet_chain_1_deadbeef_data" in guidance

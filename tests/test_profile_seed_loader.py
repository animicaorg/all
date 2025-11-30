import json

from ops.seeds import profile_loader as loader


def test_load_profile_prefers_profile_file(tmp_path):
    devnet_file = tmp_path / "devnet.json"
    fallback_file = tmp_path / "bootstrap_nodes.json"

    fallback_file.write_text(
        json.dumps(
            {"seeds": [{"peer_id": "fallback", "multiaddrs": ["/ip4/3.3.3.3/tcp/3"]}]}
        )
    )
    devnet_file.write_text(
        json.dumps(
            {"seeds": [{"peer_id": "primary", "multiaddrs": ["/ip4/2.2.2.2/tcp/2"]}]}
        )
    )

    seeds = loader.load_profile_file("devnet", seed_dir=tmp_path)
    assert [s.peer_id for s in seeds] == ["primary"]


def test_dedupe_and_write_peerstore(tmp_path):
    store_path = tmp_path / "peers.json"
    seeds = [
        loader.SeedEntry("p1", ["/ip4/1.1.1.1/tcp/1", "/ip4/1.1.1.1/tcp/1"]),
        loader.SeedEntry("p2", ["/ip4/2.2.2.2/tcp/2"]),
    ]

    deduped = loader.dedupe_multiaddrs(seeds)
    assert deduped == ["/ip4/1.1.1.1/tcp/1", "/ip4/2.2.2.2/tcp/2"]

    loader.write_peerstore(seeds, store_path)
    data = json.loads(store_path.read_text())
    peers = {p["peer_id"]: p for p in data.get("peers", [])}

    assert peers["p1"]["addrs"] == ["/ip4/1.1.1.1/tcp/1"]
    assert peers["p2"]["addrs"] == ["/ip4/2.2.2.2/tcp/2"]

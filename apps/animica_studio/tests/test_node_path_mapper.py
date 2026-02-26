from __future__ import annotations

from animica_studio.services.node_path_mapper import NodePathMapper


def test_infer_node_data_root_from_default_dir() -> None:
    assert NodePathMapper.infer_node_data_root('/var/lib/animica/da', '') == '/var'


def test_map_host_path_uses_host_chain_parent() -> None:
    mapper = NodePathMapper('/home/employee/.animica/chain-1')
    out = mapper.map_host_path('/data/da_ingest', '/data')
    assert out == '/home/employee/.animica/da_ingest'
    assert mapper.host_data_root() == '/home/employee/.animica'

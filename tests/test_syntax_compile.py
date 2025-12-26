import compileall
from pathlib import Path


def test_compile_p2p_service_and_rpc_deps() -> None:
    repo_root = Path(__file__).resolve().parents[1]
    targets = [
        repo_root / "p2p" / "node" / "p2p_service.py",
        repo_root / "rpc" / "deps.py",
    ]
    for target in targets:
        assert compileall.compile_file(str(target), quiet=1)

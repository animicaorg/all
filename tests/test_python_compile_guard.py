import compileall
from pathlib import Path


def test_python_sources_compile() -> None:
    repo_root = Path(__file__).resolve().parents[1]
    targets = [
        repo_root / "p2p",
        repo_root / "python",
    ]
    for target in targets:
        assert compileall.compile_dir(str(target), quiet=1), f"compileall failed for {target}"

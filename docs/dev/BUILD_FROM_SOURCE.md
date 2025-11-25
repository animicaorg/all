# Build From Source — Python/Rust toolchains & native flags

This guide shows how to build and run Animica **from source**, including the optional **native ZK acceleration** crate (`zk/native`) for BN254 pairing/KZG verification. You can run everything in **pure Python**; the native path is an opt-in speedup.

---

## 0) Quick matrix

| Component | Language/Tool | Notes |
|---|---|---|
| Node (core/consensus/proofs/…) | Python 3.10–3.12 | CPython, venv recommended |
| ZK native accel (`zk/native`) | Rust (stable) + pyo3/maturin | optional; enables fast BN254 pairing + KZG |
| Website / Studio (optional) | Node 18+ / pnpm | not required for node bring-up |

---

## 1) Prerequisites

### macOS (Ventura/Sonoma; Apple Silicon & Intel)
```bash
# Xcode CLT
xcode-select --install

# Homebrew (if not installed): https://brew.sh
brew update

# Python & tooling
brew install python@3.11 pyenv pipx

# Rust & build deps
brew install rustup cmake pkg-config openssl@3
rustup-init -y
# (Optional) Speed: optimize for local CPU
echo 'export RUSTFLAGS="-C target-cpu=native"' >> ~/.zshrc

Ubuntu/Debian

sudo apt-get update
sudo apt-get install -y \
  python3.11 python3.11-venv python3-pip \
  build-essential pkg-config cmake \
  libssl-dev clang curl git

# Rust
curl --proto '=https' --tlsv1.2 -sSf https://sh.rustup.rs | sh -s -- -y
# (Optional) Speed:
echo 'export RUSTFLAGS="-C target-cpu=native"' >> ~/.bashrc

Windows
	•	Recommended: WSL2 (Ubuntu) and follow the Linux steps.
	•	Native Windows builds are possible (MSVC toolchain), but WSL avoids C toolchain friction.

⸻

2) Clone & Python environment

git clone https://example.com/animica/animica.git
cd animica

# Python venv (choose your interpreter)
python3.11 -m venv .venv
source .venv/bin/activate  # Windows: .venv\Scripts\activate

# Upgrade pip & install base tooling
python -m pip install -U pip wheel setuptools

# (Optional) If the repo has extras for dev:
# python -m pip install -e ".[dev]"

You can run modules and tests without native ZK—pure Python is the default.

⸻

3) Rust toolchain

# Ensure rustup is on PATH (restart shell if needed)
rustup default stable
rustc -V
cargo -V

For Apple Silicon:

rustup target add aarch64-apple-darwin


⸻

4) Pure-Python bring-up (baseline)

Smoke some core tests without native code:

# Proofs & consensus sanity
pytest -q proofs/tests
pytest -q consensus/tests
pytest -q execution/tests

# ZK (Python backends only)
pytest -q zk/tests -k "not native"  # native is optional; suite auto-skips accel-specific checks


⸻

5) Build the native ZK accelerator (optional, recommended for speed)

The crate zk/native/ exposes a Python module animica_zk_native via pyo3/maturin, with features:
	•	pairing — BN254 Ate pairing fast path
	•	kzg — BN254 KZG commitment/opening verify
	•	python — pyo3 bindings (required for Python module)

5.1 Install maturin

python -m pip install maturin

5.2 Build & develop-install (editable)

# From repo root
maturin develop --release -m zk/native/pyproject.toml --features pairing,kzg,python

This compiles a release build and installs animica_zk_native into your current venv. On success:

python -c "import animica_zk_native as m; print('ok', m.__name__)"
# => ok animica_zk_native

5.3 Build wheels (CI/release)

maturin build --release -m zk/native/pyproject.toml --features pairing,kzg,python
# Wheels land under zk/native/target/wheels/

If you only want pairing or only KZG, drop the corresponding feature.
Example: --features pairing,python

⸻

6) Verifier auto-detection & flags
	•	Python verifiers (e.g., zk/verifiers/groth16_bn254.py, zk/verifiers/kzg_bn254.py) auto-detect the native module:
	•	If animica_zk_native is importable, they use the native fast path.
	•	Otherwise they fall back to pure Python (py_ecc/arkworks-style implementations).
	•	No special env var is required. If you want to force pure-Python, simply do not install the native module (or uninstall it):

python -m pip uninstall -y animica_zk_native



Performance knobs
	•	RUSTFLAGS="-C target-cpu=native" — enable CPU-specific SIMD/Intrinsics (local builds).
	•	CARGO_PROFILE_RELEASE_LTO=true and CARGO_PROFILE_RELEASE_CODEGEN_UNITS=1 (optional):

export CARGO_PROFILE_RELEASE_LTO=true
export CARGO_PROFILE_RELEASE_CODEGEN_UNITS=1
maturin develop --release -m zk/native/pyproject.toml --features pairing,kzg,python



⸻

7) Run tests with native engaged

# Re-activate venv if needed
source .venv/bin/activate

# Ensure native module is on sys.path
python -c "import animica_zk_native; print('native-ok')"

# ZK tests (will use native when present)
pytest -q zk/tests

# End-to-end selections
pytest -q zk/tests/test_groth16_embedding_verify.py
pytest -q zk/tests/test_plonk_poseidon_verify.py
pytest -q zk/tests/test_vk_cache.py

Benchmarks:

python zk/bench/verify_speed.py --json-out /tmp/zk_bench.json

Rust micro-bench (native crate):

pushd zk/native
cargo bench
popd


⸻

8) Optional system libraries

Some optional adapters may use these if you enable them:
	•	RocksDB backend (optional alternative DB):
	•	Ubuntu: sudo apt-get install -y librocksdb-dev
	•	macOS: brew install rocksdb
	•	Then install Python bindings as required by the chosen adapter (the repo’s code guards imports and falls back to SQLite).
	•	Blake3 (optional hash accel):

python -m pip install blake3



⸻

9) Developer quality-of-life
	•	direnv / .envrc to auto-activate venv.
	•	pre-commit hooks (if present):

python -m pip install pre-commit
pre-commit install


	•	Verbose test logging:

pytest -q -k zk --log-cli-level=INFO



⸻

10) Troubleshooting
	•	maturin errors about Python headers: ensure python3.X-dev (Linux) or Xcode CLT (macOS).
	•	Linker cannot find OpenSSL (macOS):

export LDFLAGS="-L$(brew --prefix openssl@3)/lib"
export CPPFLAGS="-I$(brew --prefix openssl@3)/include"


	•	Build uses wrong Rust target (Apple Silicon): rustup target add aarch64-apple-darwin and ensure your Python is arm64 too.
	•	Wheel import mismatch: rebuild after switching Python version; wheels are ABI-specific.
	•	Slow CI builds: prefer maturin build --release and cache ~/.cargo, target/, and wheels.

⸻

11) What’s next?
	•	Run the devnet and mine a block: see docs/dev/QUICKSTART.md.
	•	Explore ZK verifier docs: zk/docs/ and zk/bench/.
	•	Native internals: zk/native/src/ (bn254/pairing.rs, bn254/kzg.rs).

Happy hacking! 🛠️

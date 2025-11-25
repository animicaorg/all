# ISA-L linking (Linux only)

This directory documents how to link the **Intel ISA-L** (Intelligent Storage Acceleration Library) as a **system library** for Animica Native’s Reed–Solomon backend. The Rust crate exposes an optional feature flag `isal` that, when enabled, links against your **host-installed** ISA-L to accelerate erasure coding and related GF(2^8) primitives.

> **Scope**: Supported on **Linux x86_64** targets. Other platforms are not wired up in this crate.

---

## 1) Install ISA-L on your system

Pick **one** option below.

### A. Install from your distro packages (recommended)

- **Debian/Ubuntu**:
  ```bash
  sudo apt-get update
  sudo apt-get install -y libisal-dev

(Runtime package is typically libisal2 and comes as a dependency.)
	•	Fedora/RHEL/CentOS:

sudo dnf install -y isa-l isa-l-devel
# or
sudo yum install -y isa-l isa-l-devel


	•	Arch Linux:

sudo pacman -S --noconfirm isa-l
# Headers are included in the same package on many Arch-based distros.



Package names vary slightly by distro release. If your distro doesn’t ship ISA-L or you need a newer version, build from source.

B. Build from source (universal)

git clone https://github.com/intel/isa-l.git
cd isa-l
make -j"$(nproc)"
sudo make install
# Default prefix is /usr/local; headers -> /usr/local/include, libs -> /usr/local/lib
sudo ldconfig

If you installed to a non-standard prefix, export paths so the linker can find it:

export ISAL_DIR=/opt/isal
export ISAL_INCLUDE_DIR=$ISAL_DIR/include
export ISAL_LIB_DIR=$ISAL_DIR/lib
export LD_LIBRARY_PATH=$ISAL_LIB_DIR:${LD_LIBRARY_PATH:-}


⸻

2) Build Animica Native with ISA-L acceleration

Enable the isal feature on the native crate:

# From the repository root
cargo build -p animica_native --features isal --release

By default we dynamically link against libisal.so (preferred for distro-managed installs).
To statically link (e.g., for portable containers), set:

ISAL_STATIC=1 cargo build -p animica_native --features isal --release

Environment variables recognized by our build script
	•	ISAL_DIR — root prefix containing include/ and lib/
	•	ISAL_INCLUDE_DIR — header directory (overrides ISAL_DIR/include)
	•	ISAL_LIB_DIR — library directory with libisal.so / libisal.a
	•	ISAL_STATIC — if set to 1, prefer static linking (libisal.a)

The build script tries, in order: pkg-config (if available and the distro provides a .pc file), then ISAL_* vars, then common system locations.

⸻

3) Verifying the linkage
	•	Dynamic:

ldd target/release/libanimica_native.so | grep -i isal
# Expected: libisal.so => /usr/lib/... (0x...)


	•	Static:

nm -D target/release/libanimica_native.so | grep -i 'isa\|gf_'
# You should NOT see libisal as a needed shared object if statically linked.


	•	Runtime smoke test (calls into the RS path that prefers ISA-L when enabled):

cargo test -p animica_native --features isal -- --nocapture



⸻

4) Cross-compiling and musl notes

For a musl target (portable, static):

# Example for x86_64-unknown-linux-musl
rustup target add x86_64-unknown-linux-musl
# Build ISA-L with musl toolchain
CC=musl-gcc make -j"$(nproc)"  # inside isa-l/ source
make DESTDIR=/opt/isal-musl install
export ISAL_INCLUDE_DIR=/opt/isal-musl/usr/local/include
export ISAL_LIB_DIR=/opt/isal-musl/usr/local/lib
ISAL_STATIC=1 cargo build -p animica_native --features isal \
  --release --target x86_64-unknown-linux-musl


⸻

5) Troubleshooting
	•	cannot find -l:isal / headers not found
Ensure libisal-dev/isa-l-devel is installed and the library path is visible:

export ISAL_LIB_DIR=/usr/local/lib
export ISAL_INCLUDE_DIR=/usr/local/include
sudo ldconfig

Rebuild with cargo clean -p animica_native first if necessary.

	•	symbol lookup error: GLIBC_2.xx at runtime
Your host’s glibc is older than the one used to build ISA-L. Use static linking (ISAL_STATIC=1), rebuild ISA-L on the deployment host, or target musl.
	•	Illegal instruction on older CPUs
If you built ISA-L on a machine that forced very new ISA paths, rebuild on a baseline CPU (ISA-L has runtime dispatch, but mismatched toolchain/flags can still cause issues).
	•	CI/CD containers
Prefer static linking (ISAL_STATIC=1) or install libisal into the image and set LD_LIBRARY_PATH accordingly.

⸻

6) Runtime selection

At runtime, Animica’s RS backend chooses the best available implementation:
	1.	ISA-L (when isal feature is enabled and the library linked successfully)
	2.	Portable Rust fallback

No code changes are required in consumers; the choice is transparent.

⸻

7) Licensing
	•	ISA-L is distributed by Intel under a permissive BSD-style license.
	•	This repository only links to your system-provided ISA-L; see your distro’s package for its exact license text.

⸻

8) Quick checklist
	•	libisal-dev / isa-l-devel installed or ISA-L built & installed
	•	cargo build -p animica_native --features isal --release succeeds
	•	ldd libanimica_native.so shows libisal.so (dynamic) or static link confirmed
	•	Tests pass and RS encode/verify paths are exercised


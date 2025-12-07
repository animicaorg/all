# Rust Toolchain Update Helper

## Overview

The `update-rust-toolchain.sh` script helps you update your Rust toolchain to meet the minimum version requirements for building and testing Animica's Rust components.

## Requirements

**Minimum Rust version:** 1.80.0+

Some dependencies (e.g., `rayon-core v1.13.0`) require rustc 1.80.0 or later.

## Quick Start

### If you already have rustup installed:

```bash
./scripts/update-rust-toolchain.sh
```

This will:
1. Update rustup itself
2. Update all installed Rust toolchains
3. Set the default toolchain to the latest stable version

### If you don't have rustup installed:

The script will automatically download and install rustup using the official installer from https://rustup.rs/

After installation, it will configure the latest stable toolchain as your default.

## Manual Update (Alternative)

If you prefer to update manually without running the script:

```bash
# Update rustup
rustup self update

# Update all toolchains
rustup update

# Set default to stable
rustup default stable

# Verify the version
rustc --version
```

## Troubleshooting

### Script fails with "curl is required"

Install curl first:
- **Ubuntu/Debian:** `sudo apt-get install curl`
- **macOS:** curl is pre-installed
- **Windows:** Use WSL or install curl from https://curl.se/

### Permission denied error

Make sure the script is executable:
```bash
chmod +x scripts/update-rust-toolchain.sh
```

### "rustup self update" fails

This can happen if rustup was installed via a package manager instead of rustup.rs. 
In this case, update through your package manager instead, or reinstall from https://rustup.rs/

## Verifying Your Installation

After running the script, verify your Rust version:

```bash
rustc --version
# Should show: rustc 1.80.0 or higher

cargo --version
# Should show corresponding cargo version
```

## Integration with testall.sh

The `./testall.sh` script automatically checks your Rust version and will:
- Skip Rust tests if the version is below 1.80.0
- Display a clear error message with instructions
- Suggest running this update script

## More Information

- Official Rust installation guide: https://www.rust-lang.org/tools/install
- Rustup documentation: https://rust-lang.github.io/rustup/

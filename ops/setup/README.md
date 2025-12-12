# Animica Setup Script

This setup utility bootstraps a fresh Ubuntu 24.04+ host for Animica development or testnet usage. It is designed to be idempotent, non-interactive, and to provide clear logging for each step.

## Prerequisites
- Ubuntu 24.04 or newer (script will abort on non-Ubuntu hosts)
- Run as `root` (or via `sudo`)
- Outbound internet access to fetch apt packages, NodeSource binaries, and Python artifacts

## Flags
- `--clean` – Remove `.venv` and `.liboqs` before installing.
- `--with-playwright` – Install Playwright browsers and their system dependencies. Omitted by default to avoid heavy downloads.
- `--with-pq` – Build and install `liboqs` and `liboqs-python`. Optional because PQ dependencies can conflict with other environments.
- `--skip-node` – Skip Node.js installation/upgrade checks (useful on pre-provisioned hosts).
- `--skip-python` – Skip Python environment creation and package installs.
- `--skip-pnpm` – Skip `pnpm -r install` (useful when only Python pieces are needed).
- `--help` – Show usage and exit.

## Usage examples
- Standard install:
  ```bash
  sudo ./setup.sh
  ```
- Add Playwright browsers:
  ```bash
  sudo ./setup.sh --with-playwright
  ```
- Enable post-quantum tooling:
  ```bash
  sudo ./setup.sh --with-pq
  ```
- Reset environment and install Playwright + PQ support:
  ```bash
  sudo ./setup.sh --clean --with-playwright --with-pq
  ```

## Troubleshooting
- **apt lock errors**: The script retries while apt/dpkg locks are held. If you still see failures, ensure no other package managers are running, then re-run with `--clean`.
- **PQ build issues**: PQ steps are optional. When `--with-pq` is used, build logs live under `.liboqs`. If verification fails, the script cleans the PQ directory; re-run with verbose shell tracing (`bash -x ./setup.sh --with-pq`) for details.
- **Node/Playwright problems**: Use `--skip-node` or omit `--with-playwright` on headless servers that already have the required tooling.

## After installation
- Activate the Python environment:
  ```bash
  source .venv/bin/activate
  ```
- Run the Animica CLI:
  ```bash
  animica --help
  animica node up
  ```

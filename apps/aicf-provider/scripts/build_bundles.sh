#!/usr/bin/env bash
set -euo pipefail

VERSION="${1:-0.2.0}"
SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
ROOT_DIR="$(cd "${SCRIPT_DIR}/../../.." && pwd)"
APP_DIR="${ROOT_DIR}/apps/aicf-provider"
DIST_ROOT="${ROOT_DIR}/dist/provider"
PUBLIC_ROOT="${ROOT_DIR}/website/public/provider"
BUILD_ROOT="${ROOT_DIR}/.build/aicf-provider/${VERSION}"

WINDOWS_NAME="aicf-provider-worker-${VERSION}-windows-x64.zip"
LINUX_NAME="aicf-provider-worker-${VERSION}-linux-x64.tar.gz"
PYTHON_NAME="aicf-provider-worker-${VERSION}-python.tar.gz"
CHECKSUM_NAME="aicf-provider-worker-${VERSION}.sha256"
NOTES_NAME="release-notes-${VERSION}.md"

mkdir -p "${BUILD_ROOT}" "${DIST_ROOT}/windows" "${DIST_ROOT}/linux" "${DIST_ROOT}/python" "${PUBLIC_ROOT}"
rm -f "${DIST_ROOT}/windows/${WINDOWS_NAME}" "${DIST_ROOT}/linux/${LINUX_NAME}" "${DIST_ROOT}/python/${PYTHON_NAME}" "${DIST_ROOT}/${CHECKSUM_NAME}"

WINDOWS_STAGE="${BUILD_ROOT}/windows"
LINUX_STAGE="${BUILD_ROOT}/linux"
PYTHON_STAGE="${BUILD_ROOT}/python"

mkdir -p "${WINDOWS_STAGE}/logs" "${LINUX_STAGE}/logs" "${PYTHON_STAGE}/logs"

# Windows bundle content
cp "${APP_DIR}/worker.py" "${WINDOWS_STAGE}/worker.py"
cp "${APP_DIR}/provider.config.example.json" "${WINDOWS_STAGE}/provider.config.example.json"
cp "${APP_DIR}/scripts/start-worker.bat" "${WINDOWS_STAGE}/start-worker.bat"
cp "${APP_DIR}/scripts/benchmark-worker.bat" "${WINDOWS_STAGE}/benchmark-worker.bat"
cat > "${WINDOWS_STAGE}/aicf-provider-worker.exe" <<'EOT'
This placeholder executable is replaced in official signed releases.
Run build-real-exe.ps1 on Windows with PyInstaller to produce a native executable.
EOT
cat > "${WINDOWS_STAGE}/build-real-exe.ps1" <<'EOT'
Write-Host "Building native worker executable with PyInstaller..."
py -m pip install pyinstaller
py -m PyInstaller --onefile --name aicf-provider-worker worker.py
Write-Host "Executable written to dist\\aicf-provider-worker.exe"
EOT
cat > "${WINDOWS_STAGE}/README.txt" <<'EOT'
AICF Provider Worker (Windows)

1) Copy provider.config.example.json to provider.config.json and fill provider credentials.
2) Run benchmark-worker.bat.
3) Run start-worker.bat.
4) Logs are written in logs/provider-worker.log.

For signed production releases, replace aicf-provider-worker.exe with a PyInstaller build.
EOT
(
  cd "${WINDOWS_STAGE}"
  zip -qr "${DIST_ROOT}/windows/${WINDOWS_NAME}" .
)

# Linux bundle content
cp "${APP_DIR}/worker.py" "${LINUX_STAGE}/worker.py"
cp "${APP_DIR}/provider.config.example.json" "${LINUX_STAGE}/provider.config.example.json"
cp "${APP_DIR}/scripts/start-worker.sh" "${LINUX_STAGE}/start-worker.sh"
cp "${APP_DIR}/scripts/benchmark-worker.sh" "${LINUX_STAGE}/benchmark-worker.sh"
cp "${APP_DIR}/scripts/systemd/aicf-provider-worker.service" "${LINUX_STAGE}/aicf-provider-worker.service"
cat > "${LINUX_STAGE}/install-systemd.sh" <<'EOT'
#!/usr/bin/env bash
set -euo pipefail
TARGET_DIR="/opt/aicf-provider-worker"
sudo mkdir -p "${TARGET_DIR}"
sudo cp -r ./* "${TARGET_DIR}/"
sudo cp aicf-provider-worker.service /etc/systemd/system/aicf-provider-worker.service
sudo systemctl daemon-reload
sudo systemctl enable aicf-provider-worker
sudo systemctl restart aicf-provider-worker
sudo systemctl status aicf-provider-worker --no-pager
EOT
chmod +x "${LINUX_STAGE}/install-systemd.sh"
cat > "${LINUX_STAGE}/README.md" <<'EOT'
# AICF Provider Worker (Linux)

- `./benchmark-worker.sh` to benchmark hardware.
- `./start-worker.sh` to start worker loop.
- `./install-systemd.sh` to install as a service (optional).

Logs are stored at `logs/provider-worker.log`.
EOT
(
  cd "${LINUX_STAGE}"
  tar -czf "${DIST_ROOT}/linux/${LINUX_NAME}" .
)

# Python bundle content
cp "${APP_DIR}/worker.py" "${PYTHON_STAGE}/worker.py"
cp "${APP_DIR}/provider.config.example.json" "${PYTHON_STAGE}/provider.config.example.json"
cp "${APP_DIR}/requirements.txt" "${PYTHON_STAGE}/requirements.txt"
cp "${APP_DIR}/README.md" "${PYTHON_STAGE}/README.md"
cat > "${PYTHON_STAGE}/quickstart.sh" <<'EOT'
#!/usr/bin/env bash
set -euo pipefail
python3 -m venv .venv
. .venv/bin/activate
pip install -r requirements.txt
python worker.py init-config --config provider.config.json
python worker.py benchmark --config provider.config.json
python worker.py start --config provider.config.json
EOT
chmod +x "${PYTHON_STAGE}/quickstart.sh"
(
  cd "${PYTHON_STAGE}"
  tar -czf "${DIST_ROOT}/python/${PYTHON_NAME}" .
)

windows_sha="$(sha256sum "${DIST_ROOT}/windows/${WINDOWS_NAME}" | awk '{print $1}')"
linux_sha="$(sha256sum "${DIST_ROOT}/linux/${LINUX_NAME}" | awk '{print $1}')"
python_sha="$(sha256sum "${DIST_ROOT}/python/${PYTHON_NAME}" | awk '{print $1}')"

windows_size="$(stat -c %s "${DIST_ROOT}/windows/${WINDOWS_NAME}")"
linux_size="$(stat -c %s "${DIST_ROOT}/linux/${LINUX_NAME}")"
python_size="$(stat -c %s "${DIST_ROOT}/python/${PYTHON_NAME}")"

echo "${windows_sha}  ${WINDOWS_NAME}" > "${DIST_ROOT}/${CHECKSUM_NAME}"
echo "${linux_sha}  ${LINUX_NAME}" >> "${DIST_ROOT}/${CHECKSUM_NAME}"
echo "${python_sha}  ${PYTHON_NAME}" >> "${DIST_ROOT}/${CHECKSUM_NAME}"

cat > "${DIST_ROOT}/${NOTES_NAME}" <<EOT
# AICF Provider Worker ${VERSION}

## Highlights

- New unified worker CLI:
  - aicf-provider-worker init-config
  - aicf-provider-worker benchmark
  - aicf-provider-worker start
  - aicf-provider-worker health
- Bundle parity across Windows, Linux, and Python source modes.
- Built-in logs folder and benchmark output generation.

## Bundle checksums

- ${windows_sha}  ${WINDOWS_NAME}
- ${linux_sha}  ${LINUX_NAME}
- ${python_sha}  ${PYTHON_NAME}

## Notes

Windows bundle currently includes a placeholder executable and build script for generating native signed executables in release CI.
EOT

cat > "${DIST_ROOT}/manifest.json" <<EOT
{
  "source": "aicf-provider-build-script",
  "version": "${VERSION}",
  "generated_at": "$(date -u +%Y-%m-%dT%H:%M:%SZ)",
  "checksum_file": "/provider/${CHECKSUM_NAME}",
  "items": [
    {
      "platform": "windows",
      "label": "Windows GPU Worker Executable Bundle",
      "filename": "${WINDOWS_NAME}",
      "version": "${VERSION}",
      "size_bytes": ${windows_size},
      "sha256": "${windows_sha}",
      "url": "/provider/${WINDOWS_NAME}",
      "release_notes": "/provider/${NOTES_NAME}",
      "notes": "Includes worker config template, benchmark/start scripts, logs folder, and executable placeholder."
    },
    {
      "platform": "linux",
      "label": "Linux GPU Worker Bundle",
      "filename": "${LINUX_NAME}",
      "version": "${VERSION}",
      "size_bytes": ${linux_size},
      "sha256": "${linux_sha}",
      "url": "/provider/${LINUX_NAME}",
      "release_notes": "/provider/${NOTES_NAME}",
      "notes": "Includes benchmark/start scripts and systemd unit template for daemonized operation."
    },
    {
      "platform": "python",
      "label": "Python Source Bundle",
      "filename": "${PYTHON_NAME}",
      "version": "${VERSION}",
      "size_bytes": ${python_size},
      "sha256": "${python_sha}",
      "url": "/provider/${PYTHON_NAME}",
      "release_notes": "/provider/${NOTES_NAME}",
      "notes": "Source-first worker package with venv quickstart and benchmark/start commands."
    }
  ]
}
EOT

cp "${DIST_ROOT}/windows/${WINDOWS_NAME}" "${PUBLIC_ROOT}/${WINDOWS_NAME}"
cp "${DIST_ROOT}/linux/${LINUX_NAME}" "${PUBLIC_ROOT}/${LINUX_NAME}"
cp "${DIST_ROOT}/python/${PYTHON_NAME}" "${PUBLIC_ROOT}/${PYTHON_NAME}"
cp "${DIST_ROOT}/${CHECKSUM_NAME}" "${PUBLIC_ROOT}/${CHECKSUM_NAME}"
cp "${DIST_ROOT}/manifest.json" "${PUBLIC_ROOT}/manifest.json"
cp "${DIST_ROOT}/${NOTES_NAME}" "${PUBLIC_ROOT}/${NOTES_NAME}"

echo "Built provider bundles ${VERSION}"

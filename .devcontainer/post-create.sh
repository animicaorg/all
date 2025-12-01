#!/usr/bin/env bash
set -euo pipefail

#!/usr/bin/env bash
set -euo pipefail

# Devcontainer post-create bootstrap: install Rust, Node, pnpm and optionally
# Flutter/Android tools. The heavy components (Flutter/Android) are optional
# and gated by the env var `ANIMICA_INSTALL_FLUTTER` to avoid long CI runs.

apt-get update
apt-get install -y --no-install-recommends curl ca-certificates git build-essential sudo python3 python3-pip

# Install rustup (non-interactive)
if ! command -v rustc >/dev/null 2>&1; then
  curl https://sh.rustup.rs -sSf | sh -s -- -y
  export PATH="$HOME/.cargo/bin:$PATH"
fi

# Install Node.js (via NodeSource)
if ! command -v node >/dev/null 2>&1; then
  curl -fsSL https://deb.nodesource.com/setup_20.x | bash -
  apt-get install -y nodejs
fi

# Install pnpm
if ! command -v pnpm >/dev/null 2>&1; then
  npm install -g pnpm
fi

# Optionally install Flutter and Android SDK only if explicitly requested
if [ "${ANIMICA_INSTALL_FLUTTER:-0}" = "1" ]; then
  echo "ANIMICA_INSTALL_FLUTTER=1 set — installing Flutter and Android SDK (may be slow)"
  apt-get install -y --no-install-recommends git unzip xz-utils openjdk-11-jre-headless wget

  if [ ! -d "/usr/local/flutter" ]; then
    git clone https://github.com/flutter/flutter.git --depth 1 -b stable /usr/local/flutter || true
    echo 'export PATH="/usr/local/flutter/bin:$PATH"' > /etc/profile.d/flutter.sh || true
    export PATH="/usr/local/flutter/bin:$PATH"
    /usr/local/flutter/bin/flutter --version || true
  fi

  # Install Android command-line tools (best-effort)
  ANDROID_ROOT=/opt/android-sdk
  if [ ! -d "${ANDROID_ROOT}/cmdline-tools/latest" ]; then
    mkdir -p ${ANDROID_ROOT}
    cd /tmp
    CMDLINE_ZIP="commandlinetools-linux-latest.zip"
    URLS=(
      "https://dl.google.com/android/repository/commandlinetools-linux-latest.zip"
      "https://dl.google.com/android/repository/commandlinetools-linux-9477386_latest.zip"
    )
    for u in "${URLS[@]}"; do
      if wget -q -O ${CMDLINE_ZIP} "$u"; then
        break
      fi
    done
    if [ -f ${CMDLINE_ZIP} ]; then
      unzip -q ${CMDLINE_ZIP} -d ${ANDROID_ROOT}/cmdline-tools-temp || true
      mkdir -p ${ANDROID_ROOT}/cmdline-tools
      mv ${ANDROID_ROOT}/cmdline-tools-temp/cmdline-tools ${ANDROID_ROOT}/cmdline-tools/latest || true
      rm -rf ${ANDROID_ROOT}/cmdline-tools-temp ${CMDLINE_ZIP} || true
      echo "export ANDROID_SDK_ROOT=${ANDROID_ROOT}" > /etc/profile.d/android.sh
      echo "export PATH=\"${ANDROID_ROOT}/cmdline-tools/latest/bin:${ANDROID_ROOT}/platform-tools:${ANDROID_ROOT}/tools/bin:\$PATH\"" >> /etc/profile.d/android.sh
      export PATH="${ANDROID_ROOT}/cmdline-tools/latest/bin:${ANDROID_ROOT}/platform-tools:${ANDROID_ROOT}/tools/bin:$PATH"
      yes | sdkmanager --sdk_root=${ANDROID_ROOT} --licenses >/dev/null 2>&1 || true
      sdkmanager --sdk_root=${ANDROID_ROOT} --install "platform-tools" "platforms;android-33" "build-tools;33.0.0" >/dev/null 2>&1 || true
    else
      echo "Failed to download Android command-line tools; skipping Android SDK install."
    fi
  fi
else
  echo "Skipping Flutter/Android install — set ANIMICA_INSTALL_FLUTTER=1 to enable."
fi

# Install pre-commit for Python hooks (local user)
python3 -m pip install --upgrade pip
python3 -m pip install --user pre-commit

# Print versions (best-effort)
node --version || true
pnpm --version || true
rustc --version || true
python3 --version || true

exit 0

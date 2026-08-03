# Cask for the Animica Wallet desktop app (Qt). Lives at
# Casks/animica-wallet.rb in the `animicaorg/homebrew-animica` tap.
#
#   brew tap animicaorg/animica
#   brew install --cask animicaorg/animica/animica-wallet
#
# PROVENANCE (verified 2026-08-03):
#   * URL is LIVE today (HTTP 206 on a ranged GET).
#   * sha256 computed locally from the exact dmg bytes served at the URL and
#     it matches dmg_sha256 in the published manifest
#     https://animica.org/wallet/manifest.json (macos.build_label "v0.2.6",
#     architecture "arm64", 214,203,253 bytes).
#   * The dmg volume is "Animica Wallet" and contains AnimicaWallet.app.
#
# CAVEAT: the URL is UNVERSIONED (same filename is overwritten on release),
# so this sha256 will break on the next wallet release; re-pin sha256 (or move
# upstream to versioned URLs) on every update. Check
# https://animica.org/wallet/manifest.json for the current build + hash.
cask "animica-wallet" do
  version "0.2.6"
  sha256 "898b56d9c06d46d4ec91098d3076c8e1f8ebed4b5df5d02e9befa4fec773e3b0"

  url "https://animica.org/wallet/0.2.6/animica-wallet-macos.dmg"
  name "Animica Wallet"
  desc "Desktop wallet for the Animica post-quantum blockchain (ML-DSA-65)"
  homepage "https://animica.org/wallet"

  depends_on arch: :arm64
  depends_on macos: ">= :big_sur"

  app "AnimicaWallet.app"

  zap trash: [
    "~/Library/Application Support/AnimicaWallet",
    "~/Library/Preferences/org.animica.wallet.plist",
    "~/Library/Saved Application State/org.animica.wallet.savedState",
  ]

  caveats <<~EOS
    The app may not be notarized by Apple. If Gatekeeper blocks the first
    launch, right-click the app > Open, or run:
      xattr -dr com.apple.quarantine "/Applications/AnimicaWallet.app"

    SECURITY: verify the checksum after download if in doubt --
    the published manifest with the current sha256 lives at
    https://animica.org/wallet/manifest.json
  EOS
end

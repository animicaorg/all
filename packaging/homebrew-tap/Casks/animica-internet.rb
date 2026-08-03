# Cask for the Animica Internet desktop browser (.anm-only browser with a
# built-in wallet). Lives at Casks/animica-internet.rb in the
# `animicaorg/homebrew-animica` tap.
#
#   brew tap animicaorg/animica
#   brew install --cask animicaorg/animica/animica-internet
#
# PROVENANCE OF THE NUMBERS BELOW (verified 2026-08-03):
#   * version 0.1.0 read from CFBundleShortVersionString of the .app inside
#     the actual dmg (CFBundleIdentifier org.animica.internet).
#   * sha256 computed locally from the exact dmg bytes and cross-checked
#     against the published .sha256 sidecar file (they match).
#   * The build is Apple Silicon only: the main executable is a Mach-O arm64
#     binary (no x86_64 slice), LSMinimumSystemVersion 11.0.
#
# KNOWN ISSUES (must be fixed server-side before this cask can ship):
#   1. https://animica.org/internet/animica-internet-macos.dmg currently
#      returns 404 -- the /internet directory was dropped from the live web
#      root during the 2026-07-29 site redeploy. The exact artifact (matching
#      the sha256 below) exists in the server backup
#      /root/site-backups/animica.org-20260729-044756/internet/ and must be
#      restored (or re-published under a versioned path) first.
#   2. The URL is UNVERSIONED. When 0.1.1 is uploaded over the same filename
#      this cask's sha256 will mismatch and every install will fail. Strongly
#      prefer publishing versioned URLs (e.g. /internet/0.1.0/<file>) or
#      GitHub release assets, then update `url` + `livecheck` here.
cask "animica-internet" do
  version "0.1.0"
  sha256 "bd93436d4eb9ab938a0637de931f5d4130036baec23d6c66d484261472ecd4b3"

  url "https://animica.org/internet/0.1.0/animica-internet-macos.dmg"
  name "Animica Internet"
  desc ".anm-only decentralized web browser with a built-in Animica wallet"
  homepage "https://animica.org/internet"

  depends_on arch: :arm64
  depends_on macos: ">= :big_sur"

  app "AnimicaInternet.app"

  zap trash: [
    "~/Library/Application Support/AnimicaInternet",
    "~/Library/Caches/org.animica.internet",
    "~/Library/Preferences/org.animica.internet.plist",
    "~/Library/Saved Application State/org.animica.internet.savedState",
  ]

  caveats <<~EOS
    The app is ad-hoc signed (no Apple notarization). On first launch macOS
    Gatekeeper may block it; right-click the app > Open, or run:
      xattr -dr com.apple.quarantine /Applications/AnimicaInternet.app
  EOS
end

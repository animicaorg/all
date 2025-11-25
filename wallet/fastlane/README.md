# Fastlane for Animica Wallet (optional)

This folder documents optional Fastlane setups for **Android (Play Store)** and **iOS (App Store)**. It’s a lightweight pointer so you can wire CI/CD later. We don’t commit secrets here.

> If you don’t plan on publishing to stores yet, you can ignore this directory.

---

## Prerequisites

- Ruby ≥ 3.x (via rbenv/asdf) and Bundler
- Fastlane installed per platform guides:
  - Android: <https://docs.fastlane.tools/getting-started/android/>
  - iOS: <https://docs.fastlane.tools/getting-started/ios/>
- Store accounts:
  - Play Console (package: `io.animica.wallet[.dev|.test]`)
  - App Store Connect (bundle: `io.animica.wallet[.dev|.test]`)

Recommended:

```bash
# In repo root
echo 'source "https://rubygems.org"' > Gemfile
echo 'gem "fastlane"' >> Gemfile
bundle install
Run lanes with bundle exec fastlane <platform> <lane>.

Suggested structure
bash
Copy code
wallet/fastlane/
├─ README.md                      # this file
├─ Fastfile                       # (create later)
├─ Appfile                        # (create later)
├─ Matchfile                      # (optional for iOS code signing)
├─ metadata/android/              # Play Store listing (fastlane supply)
└─ Deliverfile                    # (optional for iOS deliver)
We don’t include these files by default to avoid leaking IDs/secrets. Create them locally or in CI.

Environment variables (CI-friendly)
Create a CI secret set and export before running lanes:

Android

PLAY_JSON_KEY – JSON service account (base64).
In CI: echo "$PLAY_JSON_KEY" | base64 --decode > play_key.json

ANDROID_KEYSTORE_B64, ANDROID_KEYSTORE_PASS, ANDROID_KEY_ALIAS, ANDROID_KEY_ALIAS_PASS

iOS

APP_STORE_CONNECT_API_KEY_ID, APP_STORE_CONNECT_API_ISSUER_ID, APP_STORE_CONNECT_API_KEY_BASE64

(If using match) MATCH_PASSWORD, plus a private repo for certs/profiles

Sample Fastfile (minimal)
Save as wallet/fastlane/Fastfile when ready and adapt IDs.

ruby
Copy code
default_platform(:android)

platform :android do
  desc "Build and upload Android (prod flavor) to Play internal"
  lane :deploy_internal do
    gradle(task: "clean")
    sh("flutter", "pub", "get")
    sh("flutter", "build", "appbundle", "--flavor", "prod", "--release")

    # Ensure Play service key exists in CI
    json_key = "play_key.json"
    upload_to_play_store(
      track: "internal",
      aab: "../build/app/outputs/bundle/prodRelease/app-prod-release.aab",
      json_key: json_key,
      skip_upload_images: true,
      skip_upload_screenshots: true,
      skip_upload_changelogs: false
    )
  end

  desc "Supply metadata only (listing, screenshots)"
  lane :metadata do
    supply(
      json_key: "play_key.json",
      metadata_path: "metadata/android",
      skip_upload_apk: true,
      skip_upload_aab: true
    )
  end
end

platform :ios do
  desc "Build and upload iOS (prod flavor) to TestFlight"
  lane :beta do
    # Optional: match(type: "appstore", readonly: true)
    sh("flutter", "pub", "get")
    sh("flutter", "build", "ipa", "--flavor", "prod", "--release")

    api_key = app_store_connect_api_key(
      key_id: ENV["APP_STORE_CONNECT_API_KEY_ID"],
      issuer_id: ENV["APP_STORE_CONNECT_API_ISSUER_ID"],
      key_content: Base64.decode64(ENV["APP_STORE_CONNECT_API_KEY_BASE64"])
    )

    upload_to_testflight(
      api_key: api_key,
      ipa: "../build/ios/ipa/*.ipa",
      skip_waiting_for_build_processing: true
    )
  end
end
Play Store metadata (Android)
Initialize listing files:

bash
Copy code
# from wallet/fastlane
mkdir -p metadata/android/en-US
cat > metadata/android/en-US/title.txt <<EOF
Animica Wallet
EOF
cat > metadata/android/en-US/short_description.txt <<EOF
Deterministic Python-VM wallet with PQ-ready keys.
EOF
cat > metadata/android/en-US/full_description.txt <<'EOF'
Animica Wallet is a cross-platform wallet for the Animica network:
• Deterministic Python-VM transactions
• Post-quantum key stubs (Dilithium3/SPHINCS+)
• Light/dark themes, multi-platform builds
EOF
Run: bundle exec fastlane android metadata

App Store metadata (iOS)
Use deliver init (locally) to pull/push listing content, or manage listings in App Store Connect UI.
For TestFlight-only flows, deliver is optional.

Build notes
Flavors: lanes above assume Flutter flavors: dev | test | prod

Signing:

Android: configure android/key.properties (not committed), reference in build.gradle

iOS: use automatic signing or match with a private repo

Versioning: bump in pubspec.yaml (version: x.y.z+build); some stores require monotonic build numbers

Typical flows
Play internal test:

bash
Copy code
(cd wallet/fastlane && bundle exec fastlane android deploy_internal)
TestFlight beta:

bash
Copy code
(cd wallet/fastlane && bundle exec fastlane ios beta)
Troubleshooting
Play API permission errors: verify service account has “Release Manager” and the package name matches.

iOS build fails signing: confirm profiles/certs, try Xcode archive once to warm caches, or adopt match.

Flutter artifacts missing: run flutter doctor -v and flutter pub get before lanes.

Security reminder: never commit keystores, provisioning profiles, or API keys. Keep secrets in CI or local keychains.

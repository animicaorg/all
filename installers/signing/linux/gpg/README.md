# Linux Repo Signing (Optional)

This doc explains how we **sign Linux packages and repositories** with OpenPGP (GPG), where keys live in CI, and how users verify and install our key safely.

> TL;DR  
> - Prefer **signing repository metadata** (APT `InRelease`, YUM/DNF `repomd.xml`).  
> - Per-package signing is optional: `.rpm` is signed by design; `.deb` can be signed with `dpkg-sig` but is usually unnecessary if the repo metadata is signed.  
> - Never commit private keys. Store them as **base64-encoded** CI secrets.

---

## 0) Keys & secrets

We use **one signing identity per channel** (e.g., `stable`, `beta`) to simplify revocation/rotation.

Recommended key type:
- **ed25519 (sign-only)** primary key with **2y** expiry; rotate before expiry.  
- Optionally create a separate **signing subkey**; keep the primary offline.

**Secrets in GitHub Actions:**
- `DEB_GPG_PRIVATE_KEY_ASC_BASE64`, `DEB_GPG_PASSPHRASE`
- `RPM_GPG_PRIVATE_KEY_ASC_BASE64`, `RPM_GPG_PASSPHRASE`

Publish the **public key** at a stable HTTPS URL (e.g., `https://downloads.animica.org/linux/keys/animica.asc`).

---

## 1) Create a key (maintainer workstation)

```bash
gpg --quick-generate-key "Animica Linux Signing (stable) <linux-stable@animica.dev>" ed25519 sign 2y
# List keys and copy KEYID:
gpg --list-keys
# Export public key:
gpg --armor --export KEYID > animica-stable.asc
# Export private (keep secret; store in vault/CI as base64):
gpg --armor --export-secret-keys KEYID > animica-stable-private.asc
base64 -w0 animica-stable-private.asc > animica-stable-private.asc.b64
# Prepare a revocation certificate and store offline:
gpg --output revocation-cert.asc --gen-revoke KEYID


⸻

2) CI: Import & trust the key (common steps)

# Import (APT):
echo "$DEB_GPG_PRIVATE_KEY_ASC_BASE64" | base64 -d > deb-priv.asc
gpg --batch --yes --import deb-priv.asc
KEYID_DEB="$(gpg --with-colons --list-secret-keys | awk -F: '/^sec:/ {print $5; exit}')"
# Mark ultimate trust non-interactively:
printf '5\ny\n' | gpg --command-fd 0 --batch --yes --edit-key "$KEYID_DEB" trust quit

# Import (RPM):
echo "$RPM_GPG_PRIVATE_KEY_ASC_BASE64" | base64 -d > rpm-priv.asc
gpg --batch --yes --import rpm-priv.asc
KEYID_RPM="$(gpg --with-colons --list-secret-keys | awk -F: '/^sec:/ {print $5; exit}')"
printf '5\ny\n' | gpg --command-fd 0 --batch --yes --edit-key "$KEYID_RPM" trust quit

When scripting signatures, use loopback pinentry:

GPG_PIN="--pinentry-mode loopback --passphrase"


⸻

3) APT repository signing (Debian/Ubuntu)

3.1 Build metadata and sign

From the repo root (directory that has dists/ or where you generate Release):

# Generate Release file (example with apt-ftparchive)
apt-ftparchive release . > Release

# Create clear-signed InRelease (preferred) and detached Release.gpg
gpg --batch --yes --digest-algo sha256 --armor \
  --pinentry-mode loopback --passphrase "$DEB_GPG_PASSPHRASE" \
  --clearsign -o InRelease Release

gpg --batch --yes --digest-algo sha256 --armor \
  --pinentry-mode loopback --passphrase "$DEB_GPG_PASSPHRASE" \
  --detach-sign -o Release.gpg Release

Tip: If you use reprepro or aptly, enable their signing hooks to produce InRelease automatically.

3.2 Optional: sign .deb packages

# dpkg-sig (optional; repository signing is generally sufficient)
dpkg-sig --sign builder --gpg-options="--pinentry-mode loopback --passphrase $DEB_GPG_PASSPHRASE" path/to/*.deb


⸻

4) YUM/DNF repository signing (RHEL/Fedora/CentOS)

4.1 Sign RPMs (rpmsign)

Configure ~/.rpmmacros in CI:

cat >> ~/.rpmmacros <<MAC
%_signature gpg
%_gpg_name $KEYID_RPM
%_gpg_digest_algo sha256
%__gpg_sign_cmd_extra_args --pinentry-mode loopback --passphrase $RPM_GPG_PASSPHRASE
MAC

rpmsign --addsign path/to/*.rpm

4.2 Create repo metadata and sign repomd.xml

createrepo_c repo/   # or createrepo
gpg --batch --yes --armor \
  --pinentry-mode loopback --passphrase "$RPM_GPG_PASSPHRASE" \
  --detach-sign -o repo/repodata/repomd.xml.asc repo/repodata/repomd.xml

Ensure your .repo file sets gpgcheck=1 and points to the public key URL.

⸻

5) User installation (safe key handling)

Debian/Ubuntu (APT)

sudo install -d -m 0755 /etc/apt/keyrings
curl -fsSL https://downloads.animica.org/linux/keys/animica.asc | sudo tee /etc/apt/keyrings/animica.asc >/dev/null
sudo chmod 0644 /etc/apt/keyrings/animica.asc

echo "deb [arch=amd64 signed-by=/etc/apt/keyrings/animica.asc] https://downloads.animica.org/apt stable main" | \
  sudo tee /etc/apt/sources.list.d/animica.list >/dev/null

sudo apt update

Avoid apt-key; it is deprecated. Use a keyring file and signed-by= as shown.

RHEL/Fedora/CentOS (DNF/YUM)

sudo rpm --import https://downloads.animica.org/linux/keys/animica.asc
sudo tee /etc/yum.repos.d/animica.repo >/dev/null <<'REPO'
[animica]
name=Animica
baseurl=https://downloads.animica.org/yum/$basearch
enabled=1
gpgcheck=1
repo_gpgcheck=1
gpgkey=https://downloads.animica.org/linux/keys/animica.asc
REPO

sudo dnf makecache


⸻

6) Verification snippets (debug)

# APT metadata
gpg --verify InRelease 2>&1 | grep 'Good signature' || true

# RPM metadata
gpg --verify repodata/repomd.xml.asc repodata/repomd.xml

# An RPM
rpm --checksig -v path/to/pkg.rpm

# A DEB (if signed)
dpkg-sig --verify path/to/pkg.deb


⸻

7) Rotation & revocation
	•	Rotate keys ahead of expiry; publish the new public key and ship repo metadata signed by both (bridge window).
	•	If a key is compromised:
	1.	Publish a revocation certificate.
	2.	Remove the old key from served key URLs.
	3.	Re-sign all metadata with the new key.
	4.	Announce widely (site, release notes).

⸻

8) CI examples (GitHub Actions)

- name: Import DEB signing key
  run: |
    echo "$DEB_GPG_PRIVATE_KEY_ASC_BASE64" | base64 -d > deb-priv.asc
    gpg --batch --yes --import deb-priv.asc
    KEYID_DEB="$(gpg --with-colons --list-secret-keys | awk -F: '/^sec:/ {print $5; exit}')"
    printf '5\ny\n' | gpg --command-fd 0 --batch --yes --edit-key "$KEYID_DEB" trust quit

- name: Sign APT metadata
  env: { DEB_GPG_PASSPHRASE: ${{ secrets.DEB_GPG_PASSPHRASE }} }
  run: |
    apt-ftparchive release . > Release
    gpg --batch --yes --digest-algo sha256 --armor --pinentry-mode loopback --passphrase "$DEB_GPG_PASSPHRASE" --clearsign -o InRelease Release
    gpg --batch --yes --digest-algo sha256 --armor --pinentry-mode loopback --passphrase "$DEB_GPG_PASSPHRASE" --detach-sign -o Release.gpg Release

- name: Import RPM signing key
  run: |
    echo "$RPM_GPG_PRIVATE_KEY_ASC_BASE64" | base64 -d > rpm-priv.asc
    gpg --batch --yes --import rpm-priv.asc
    KEYID_RPM="$(gpg --with-colons --list-secret-keys | awk -F: '/^sec:/ {print $5; exit}')"
    printf '5\ny\n' | gpg --command-fd 0 --batch --yes --edit-key "$KEYID_RPM" trust quit

- name: Sign RPMs & repo metadata
  env: { RPM_GPG_PASSPHRASE: ${{ secrets.RPM_GPG_PASSPHRASE }} }
  run: |
    cat >> ~/.rpmmacros <<MAC
    %_signature gpg
    %_gpg_name $KEYID_RPM
    %_gpg_digest_algo sha256
    %__gpg_sign_cmd_extra_args --pinentry-mode loopback --passphrase $RPM_GPG_PASSPHRASE
    MAC
    rpmsign --addsign repo/*.rpm
    createrepo_c repo/
    gpg --batch --yes --armor --pinentry-mode loopback --passphrase "$RPM_GPG_PASSPHRASE" --detach-sign -o repo/repodata/repomd.xml.asc repo/repomd.xml


⸻

9) Security notes
	•	Keep private keys out of the repo. Use environment-scoped CI secrets with required reviewers.
	•	GPG passphrases must not appear in logs. Use masked secrets.
	•	Prefer separate keys per channel or product to reduce blast radius.
	•	Serve the public key via HTTPS and pin it in docs; consider publishing the key fingerprint in multiple places.

⸻

10) Files & locations (repo)
	•	Public keys (served): downloads/linux/keys/*.asc (outside this repo; deployed artifacts)
	•	CI config & scripts: this README
	•	(No private keys in VCS.)


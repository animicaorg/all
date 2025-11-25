# Flatpak Portal Permissions — Practical Guidelines

This guide explains **which permissions to grant** in your Flatpak manifests and
**how to use portals** instead of breaking out of the sandbox. It includes
minimal baselines, wallet/explorer examples, and QA tips.

> TL;DR — Prefer **portals**. Avoid direct `--filesystem=host`, `--device=all`,
> or broad `--talk-name=*`. Only grant what you can **prove** you need.

---

## 0) Core concepts

- **Sandbox**: Your app runs isolated; permissions are added via `finish-args`.
- **Portals**: DBus interfaces mediated by `xdg-desktop-portal` that let you
  open files, show notifications, access the keyring, open URIs, etc., **without**
  global host access.
- **Runtime**: Use a current Freedesktop/GNOME/KDE runtime so portal helpers are up-to-date.

Useful references:
- File chooser & save: `org.freedesktop.portal.FileChooser`
- Open links / handlers: `org.freedesktop.portal.OpenURI`
- Secrets/keyring: `org.freedesktop.secrets` (Secret Service API)
- Notifications: `org.freedesktop.portal.Notification`
- Screenshots / screencast: `org.freedesktop.portal.Screencast` (avoid unless required)

---

## 1) Minimal baseline (networked desktop app)

Grant only what’s typically necessary for a GUI network client:

```yaml
# In your Flatpak manifest (finish-args)
finish-args:
  # Display servers (prefer Wayland; allow fallback X11 for older environments)
  - --socket=wayland
  - --socket=fallback-x11

  # Network access (JSON-RPC, websockets, HTTP(S), etc.)
  - --share=network

  # DBus user bus (needed for portals themselves)
  - --socket=session-bus

  # Open URLs, file pickers, notifications — via portals (no extra flags needed)
  # OPTIONAL: request secret service access for credentials storage
  - --talk-name=org.freedesktop.secrets

Do not add any --filesystem unless absolutely necessary; use the FileChooser portal.

⸻

2) Filesystem access — use portals, not host mounts

Prefer (good):
	•	Use the FileChooser portal for user-selected files and directories.
	•	Cache in the app sandbox at ~/.var/app/<app-id>/.

Avoid (bad):
	•	--filesystem=host or --filesystem=home (overly broad).
	•	--persist= unless you know exactly why (keeps data across updates, but be explicit).

If you must:

Grant the narrowest path and read-only if possible:

# Example: read-only access to Downloads (try to avoid)
- --filesystem=xdg-download:ro


⸻

3) Key storage & secrets

Use the Secret Service API via org.freedesktop.secrets. Most desktops
provide a service (e.g., GNOME Keyring, KWallet).

- --talk-name=org.freedesktop.secrets

Implementation options:
	•	Use a library that talks to the Secret Service API (e.g., libsecret).
	•	If your runtime/UI toolkit (Tauri/Electron/Flutter) provides keyring helpers,
ensure they use the portal/secret service rather than raw files.

Avoid shipping your own credential store unless encrypted and scoped to sandbox.

⸻

4) Opening links, default browser, deep links

Use the OpenURI portal. Most toolkits do this automatically when calling
“open external URL”. No extra finish-args required beyond session bus.

Do not request --talk-name=org.freedesktop.portal.Desktop directly; instead
use your toolkit’s portal integration.

⸻

5) Notifications

Use Notification portal — no custom permissions required beyond DBus session bus.
Avoid direct access to host notification services.

⸻

6) Hardware devices (USB, camera, etc.)

Default: none — no device access.

Only enable device access for a strict, proven need. Examples:
	•	Hardware wallets (future): prefer talking to a bridge/helper app outside the sandbox
or a well-scoped udev/device portal when available. Avoid --device=all.
	•	Camera/mic/screen: use Camera, Microphone, Screencast portals — they prompt users.
Do not add --device=all or --socket=system-bus.

⸻

7) DBus rules

Portals require session bus; you rarely need custom bus access.

Avoid broad --talk-name=* or --own-name=*.

Allow only named services you depend on, e.g.:

# For Secret Service API
- --talk-name=org.freedesktop.secrets


⸻

8) Wallet app (example policy)

For a wallet that connects to a node over HTTP(S)/WS, uses notifications,
stores an API token in keyring, and allows user to import/export files:

app-id: io.animica.Wallet
# ...
finish-args:
  - --socket=wayland
  - --socket=fallback-x11
  - --share=network
  - --socket=session-bus
  - --talk-name=org.freedesktop.secrets
  # Optional: if you insist on a convenience path (prefer FileChooser portal)
  # - --filesystem=xdg-download

Design your app to always prompt via FileChooser for import/export.
Do not read arbitrary dotfiles from $HOME.

⸻

9) Explorer desktop (example policy)

A packaged webview that loads a remote/local URL; needs network, open links:

app-id: io.animica.Explorer
# ...
finish-args:
  - --socket=wayland
  - --socket=fallback-x11
  - --share=network
  - --socket=session-bus
  # No secrets, no filesystem, no devices by default

If you implement “Save as CSV” — use FileChooser save portal; no filesystem grants.

⸻

10) Debugging portals
	•	Ensure xdg-desktop-portal is running:
systemctl --user status xdg-desktop-portal.service
	•	Inspect portal logs:
journalctl --user -u xdg-desktop-portal -f
	•	Check granted permissions:
flatpak info --show-permissions <app-id>
	•	Temporarily override for testing (remove after):
flatpak override --user --filesystem=xdg-download:ro <app-id>

⸻

11) Common anti-patterns to avoid
	•	--filesystem=host — leaks user data; defeats sandbox.
	•	--device=all — grants access to all hardware (dangerous).
	•	--socket=system-bus — system-level DBus is rarely needed.
	•	Broad --talk-name=* — noisy & risky; specify exact names if necessary.

⸻

12) QA checklist
	•	Launch on Wayland and X11; verify file open/save via portal prompts.
	•	Paste & click external links; verify OpenURI portal opens the default browser.
	•	Trigger notifications; confirm permission prompt and rendering.
	•	Store/retrieve a key using Secret Service; confirm it appears in keyring UI.
	•	Remove all --filesystem grants and re-test basic flows (should still work).
	•	Run flatpak info --show-permissions to confirm minimal set before release.

⸻

13) Manifest snippet reference

Add to your io.animica.Wallet.yml or io.animica.Explorer.yml:

# Excerpt
finish-args:
  - --socket=wayland
  - --socket=fallback-x11
  - --share=network
  - --socket=session-bus
  - --talk-name=org.freedesktop.secrets  # only if you actually store secrets

Keep your runtime up to date to get latest portal features/bugfixes.

⸻

Rule of least privilege: Start with no permissions, add one by one
only when a feature provably needs it, and prefer portals always.


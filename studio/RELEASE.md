# Release staging — animica 2.0.0 (Animica Studio)

`python/pyproject.toml` is bumped to **2.0.0** (additive: the new `animica.studio`
serverless SDK ships inside the existing package; nothing removed). This file is
the runbook for cutting the release. **The irreversible/global steps are gated.**

## ⚠️ Before anything: rotate the leaked credentials

A PyPI token and a GitHub PAT were pasted in plaintext in the working session and
must be considered compromised. **Revoke + reissue both before publishing:**

- PyPI:   https://pypi.org/manage/account/token/   (delete the old token, mint a project-scoped one for `animica`)
- GitHub: https://github.com/settings/tokens         (revoke the `ghp_…`, issue a fine-grained PAT)

Do not paste the new token into a shell history or a file. Use `~/.pypirc` with
locked-down perms, or `TWINE_PASSWORD` via an env var set out-of-band, or
`keyring`.

## 1. Pre-flight (safe, run anytime)

```bash
cd /root/animica/python
# clean build
rm -rf dist build
/root/animica/.venv/bin/python -m pip install --quiet build twine
/root/animica/.venv/bin/python -m build            # builds sdist + wheel into dist/
/root/animica/.venv/bin/python -m twine check dist/*   # metadata/readme lint
```

Then verify a clean install in a throwaway venv actually imports Studio and the
existing CLI (catch breakage BEFORE the world sees 2.0.0):

```bash
python3 -m venv /tmp/anm-rel && /tmp/anm-rel/bin/pip install --quiet dist/animica-2.0.0-*.whl
/tmp/anm-rel/bin/python -c "import animica.studio as s; print('studio', s.__version__)"
/tmp/anm-rel/bin/animica studio --help    # run/deploy/serve/fn present, lifecycle intact
```

## 2. Publish to PyPI — GATED (irreversible, global)

A published version can never be re-uploaded or edited. This bumps the package
**every existing `pip install animica` user** gets. Only run after step 1 is
green and the credentials are rotated:

```bash
# TWINE_USERNAME=__token__ and TWINE_PASSWORD=<new pypi-… token> set out-of-band
/root/animica/.venv/bin/python -m twine upload dist/animica-2.0.0-*.whl dist/animica-2.0.0.tar.gz
```

Consider `--repository testpypi` first to dry-run the upload end to end.

## 3. GitHub — GATED

The Studio work is on branch `studio-qt-agent-overhaul`. Commit, push with a
**rotated** PAT (or your existing SSH/credential helper — do not bake a token
into the remote URL), and open a PR to `main`:

```bash
cd /root/animica
git add studio/ python/animica/studio/ python/animica/cli/studio_serverless.py \
        python/animica/cli/studio.py rpc/methods/fn.py \
        provider-daemon/src/adapters/function.ts provider-daemon/src/lib/runtime.ts \
        website/src/components/StudioBanner.astro website/src/pages/index.astro \
        website/src/pages/studio.astro website/public/llms.txt \
        animica-mcp-py/src/animica_mcp/server.py python/pyproject.toml
git commit            # message in the commit body below
git push origin studio-qt-agent-overhaul
gh pr create --base main --title "Animica Studio: serverless compute (2.0.0)" --body-file -
```

## 4. studio.animica.org cutover — GATED

See `studio/deploy/CUTOVER.md`. The new site has **no remote terminal** (the old
noVNC `:8123` broker is removed). The live nginx flip is an operator step.

---

**Why these are gated and not auto-fired:** publishing 2.0.0 and flipping a live
domain on the host that also runs the authoritative verifier seed are
irreversible and outward-facing. They run after the code is built, tested, and
the credentials are rotated — not in the same breath as writing it.

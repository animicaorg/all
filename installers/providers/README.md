# AICF Provider Installers

Provider bundle build entrypoint:

```bash
./installers/providers/build_release.sh 0.2.0
```

Per-platform helpers:

- `installers/providers/linux/build_release.sh`
- `installers/providers/python/build_release.sh`
- `installers/providers/windows/build_release.ps1`

Artifacts are produced in `dist/provider/*` and mirrored to `website/public/provider`.

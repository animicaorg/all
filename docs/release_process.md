# Animica Release Process

Follow these steps whenever cutting a mainnet-ready Animica release.

1. **Run the full test suite**
   - Execute `./testall.sh` from the repository root and ensure all checks pass.
2. **Bump version numbers**
   - Update any platform, package, and image versions across `package.json`, crates, Docker tags, and installer metadata.
3. **Create an annotated git tag**
   - Tag the release with the final semantic version (for example, `vX.Y.Z`) and push the tag to origin.
4. **Build binaries and Docker images**
   - Produce platform binaries via the installers pipeline and build multi-arch images for the node, explorer, wallet, and supporting services.
5. **Upload release assets**
   - Attach the signed binaries, archives, SBOMs, and Docker image digests to the GitHub release so they are discoverable for users.
6. **Update marketplace manifest files**
   - Refresh the published manifests (wallet, explorer, extension, and contract metadata) with the new version numbers, icons, and download URLs so stores and launchers pick up the release.

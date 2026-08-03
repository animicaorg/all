# Formula for the Animica CLI (PyPI package `animica`).
#
# Tap layout: this file lives at Formula/animica.rb in the
# `animicaorg/homebrew-animica` tap repo. Install with:
#
#   brew tap animicaorg/animica
#   brew install animicaorg/animica/animica
#
# DESIGN NOTE / TRADEOFF (read before editing):
# The upstream package declares ~60 runtime dependencies, INCLUDING the full
# AI stack (torch, transformers, diffusers, sentence-transformers, ...) as
# *base* dependencies by design (the same package powers node, miner, ENA
# training and media serving). A faithful homebrew-core-style resource graph
# would need 200+ `resource` stanzas (the transitive closure of torch alone),
# which is impractical to hand-maintain for every release. This tap formula
# therefore uses the "pip install from the verified sdist" pattern instead:
# Homebrew downloads + checksums the sdist (sha256 below was computed from the
# actual PyPI artifact), and pip resolves the dependency graph from PyPI at
# install time inside the formula's private virtualenv.
#
# Consequences (accepted for a third-party tap, NOT acceptable for
# homebrew-core):
#   * network access during `brew install` (pip contacts PyPI),
#   * transitive dependencies are not individually checksummed by Homebrew
#     (pip verifies its own hashes from the PyPI index),
#   * the install is large: expect roughly 2-4 GB in the keg because of the
#     AI/torch stack.
class Animica < Formula
  include Language::Python::Virtualenv

  desc "Post-quantum L1 blockchain CLI: node, wallet, miner, contracts, AI jobs"
  homepage "https://animica.org"
  url "https://files.pythonhosted.org/packages/e6/26/54dc006f8ceae224a5f274f4170d94a31c022cb26dc56b7251a22e9f5eec/animica-9.0.8.tar.gz"
  sha256 "429a4c270f33847cb1d0954dae2dea2e13b3830199a55eddb76e14d36e1fc712"
  license "Apache-2.0"

  depends_on "python@3.12"

  def install
    venv = virtualenv_create(libexec, "python3.12")
    # Upgrade pip first so modern wheels (metadata 2.2+) resolve cleanly.
    system libexec/"bin/python", "-m", "pip", "install", "--upgrade", "pip"
    # Install the checksummed sdist we were handed by Homebrew (buildpath is
    # the unpacked tarball). pip pulls the dependency graph from PyPI -- see
    # the tradeoff note in the header.
    system libexec/"bin/python", "-m", "pip", "install", buildpath.to_s
    bin.install_symlink libexec/"bin/animica"
  end

  def caveats
    <<~EOS
      The animica package intentionally ships its full stack (node + wallet +
      miner + AI/ENA). The install is several GB because of the torch/AI
      dependencies. Dependency resolution happens against PyPI during
      `brew install` (network required).

      Quick start:
        animica --version
        animica node status --network mainnet
        animica wallet create
    EOS
  end

  test do
    assert_match(/animica \d+\.\d+\.\d+/, shell_output("#{bin}/animica --version"))
  end
end

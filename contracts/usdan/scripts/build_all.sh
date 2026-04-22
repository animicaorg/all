#!/usr/bin/env bash
set -euo pipefail

mkdir -p \
  contracts/build/usdan_token \
  contracts/build/usdan_mint_controller \
  contracts/build/usdan_redemption_controller \
  contracts/build/usdan_compliance_controller \
  contracts/build/usdan_reserve_attestation

python -m vm_py.cli.compile --manifest contracts/packages/usdan_token/manifest.json --out contracts/build/usdan_token/usdan_token.ir
python -m vm_py.cli.compile --manifest contracts/packages/usdan_mint_controller/manifest.json --out contracts/build/usdan_mint_controller/usdan_mint_controller.ir
python -m vm_py.cli.compile --manifest contracts/packages/usdan_redemption_controller/manifest.json --out contracts/build/usdan_redemption_controller/usdan_redemption_controller.ir
python -m vm_py.cli.compile --manifest contracts/packages/usdan_compliance_controller/manifest.json --out contracts/build/usdan_compliance_controller/usdan_compliance_controller.ir
python -m vm_py.cli.compile --manifest contracts/packages/usdan_reserve_attestation/manifest.json --out contracts/build/usdan_reserve_attestation/usdan_reserve_attestation.ir

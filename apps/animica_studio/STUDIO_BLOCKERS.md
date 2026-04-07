# Animica Studio Blockers

## Open Blockers
- Live RPC/operator validation is still missing for balance refresh, tx send, node/sync truth, mining telemetry, AICF credits/claim, and ENA publish against a real node stack.
- Packaged-mode validation is still missing. Qt plugin/resource lookup, CLI discovery in packaged mode, and macOS/Linux bundle launch behavior have not yet been exercised end-to-end.
- DA/ENA local-ingest depends on real host-to-node mount visibility. Studio now chooses a writable host path, but a real operator deployment still needs mount verification on the target machine.
- Legacy config migration should still be tested against real long-lived `config.json` files from older Studio installs.

## Recently Resolved
- `MainWindow` constructor hang caused by synchronous console/CLI capability probing
- read-only app-data startup failures
- deleted-object worker signal crashes during shutdown smoke
- missing real Create Wallet action
- stale 18-decimal ANM assumptions
- ENA local-ingest failure caused by unwritable hardcoded host path mapping
- combined ENA GUI smoke abort caused by `QCoreApplication` vs `QApplication` test interaction

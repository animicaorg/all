# DA CLI Quick Reference

## New Commands Summary

### Data Operations
```bash
animica da put --file data.bin [--namespace N] [--json]          # Submit blob (alias for submit)
animica da proof COMMITMENT [--verify] [--json]                   # Generate or verify DA proof
```

### Storage Management
```bash
animica da storage register --bytes N --endpoint URL|PATH [--json]  # Register storage contributor
animica da storage list [--json]                                     # List storage contributors
animica da storage heartbeat [--id ID] [--json]                      # Send heartbeat
```

### Checkpoints
```bash
animica da checkpoints list [--namespace NS] [--limit N] [--json]  # List checkpoints
animica da checkpoints verify COMMITMENT [--json]                   # Verify checkpoint
```

## Implementation Stats
- **Total Commands:** 10 (3 existing + 7 new)
- **Subcommand Groups:** 2 (storage, checkpoints)
- **Lines of Code:** ~1100
- **JSON Output:** All commands support --json flag
- **URL Normalization:** All commands use normalize_rpc_url
- **Error Handling:** Comprehensive with user-friendly messages
- **Security:** Path validation and write permission checks

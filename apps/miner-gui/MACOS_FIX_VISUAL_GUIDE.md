# Visual Guide: macOS Infinite Spawning Fix

## Before Fix ❌

```python
"""Main entry point for the Animica GUI Miner application."""

import logging
import multiprocessing
import sys
from pathlib import Path

from PySide6.QtGui import QIcon
from PySide6.QtWidgets import QApplication

# Set up logging
logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s [%(levelname)s] %(name)s: %(message)s",
    datefmt="%Y-%m-%d %H:%M:%S"
)

logger = logging.getLogger(__name__)


def main() -> int:
    """Main entry point for the GUI miner."""
    # ... application code ...
    return app.exec()


if __name__ == "__main__":
    # ⚠️ PROBLEM: freeze_support() is inside the guard
    # This doesn't work correctly with PyInstaller on macOS!
    multiprocessing.freeze_support()
    sys.exit(main())
```

### What Happens on macOS
1. User double-clicks the .app bundle
2. PyInstaller's bootloader starts
3. The `if __name__ == "__main__"` guard may not execute as expected
4. `freeze_support()` is never called
5. When multiprocessing is used (in device detection or mining), it spawns new processes
6. Each new process tries to start again
7. **Result**: Infinite spawning! 💥

---

## After Fix ✅

```python
"""Main entry point for the Animica GUI Miner application."""

import logging
import multiprocessing
import sys
from pathlib import Path

from PySide6.QtGui import QIcon
from PySide6.QtWidgets import QApplication

# ✅ FIX: Call freeze_support() at module level
# Required for PyInstaller frozen executables on macOS/Windows to prevent
# infinite process spawning when using multiprocessing module.
# MUST be called at module level, not inside if __name__ == "__main__".
multiprocessing.freeze_support()

# Set up logging
logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s [%(levelname)s] %(name)s: %(message)s",
    datefmt="%Y-%m-%d %H:%M:%S"
)

logger = logging.getLogger(__name__)


def main() -> int:
    """Main entry point for the GUI miner."""
    # ... application code ...
    return app.exec()


if __name__ == "__main__":
    # ✅ Clean guard - no multiprocessing calls here
    sys.exit(main())
```

### What Happens on macOS Now
1. User double-clicks the .app bundle
2. PyInstaller's bootloader starts
3. Module is imported
4. **`freeze_support()` executes immediately** at module level ✓
5. Logging is configured
6. Main function is called
7. Application starts normally
8. **Result**: Single instance, works perfectly! ✨

---

## Key Differences

| Aspect | Before (❌) | After (✅) |
|--------|------------|-----------|
| `freeze_support()` location | Inside `if __name__` guard | Module level (top-level) |
| Execution timing | Conditional (may not run) | Always runs when module loads |
| macOS .app bundle | Fails (infinite spawn) | Works correctly |
| PyInstaller compatibility | ❌ Broken | ✅ Correct |
| Best practice | ❌ No | ✅ Yes |

---

## Why Module-Level Works

```
┌─────────────────────────────────────┐
│ PyInstaller Bootloader Starts       │
│ (macOS .app bundle double-clicked)  │
└────────────────┬────────────────────┘
                 │
                 ▼
┌─────────────────────────────────────┐
│ Python Interpreter Initialized      │
└────────────────┬────────────────────┘
                 │
                 ▼
┌─────────────────────────────────────┐
│ Module Imported (main.py)           │
│                                     │
│ ✅ freeze_support() executes HERE   │
│    (module-level code)              │
└────────────────┬────────────────────┘
                 │
                 ▼
┌─────────────────────────────────────┐
│ if __name__ == "__main__" evaluated │
│ (may or may not be true)            │
└────────────────┬────────────────────┘
                 │
                 ▼
┌─────────────────────────────────────┐
│ Application Runs Safely             │
│ No infinite spawning!               │
└─────────────────────────────────────┘
```

---

## Testing the Fix

### Unit Tests
```bash
cd apps/miner-gui
pytest animica_miner_gui/tests/test_main_entry.py -v
```

Expected output:
```
test_freeze_support_at_module_level PASSED ✓
test_main_function_signature SKIPPED
test_multiprocessing_import_at_top PASSED ✓
test_main_has_proper_guard PASSED ✓
```

### Build and Manual Test
```bash
cd apps/miner-gui
./build-scripts/build_macos.sh

# After build completes:
open dist/Animica-Miner-GUI-*-macOS-*.dmg

# Mount DMG and copy to Applications
# Launch the app - should open only ONCE
```

---

## References

- [Python multiprocessing.freeze_support()](https://docs.python.org/3/library/multiprocessing.html#multiprocessing.freeze_support)
- [PyInstaller Recipe: Multiprocessing](https://pyinstaller.org/en/stable/common-issues-and-pitfalls.html#multi-processing)
- Issue: "The miner gui wallet still opens infinitely on Mac"

---

## Summary

**The Fix in One Line:**
```python
multiprocessing.freeze_support()  # ← Move this to the TOP of the file!
```

This simple change prevents infinite spawning on macOS by ensuring `freeze_support()` runs before any multiprocessing operations when the .app bundle is launched.

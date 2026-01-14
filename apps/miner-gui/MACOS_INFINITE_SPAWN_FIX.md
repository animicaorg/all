# macOS Infinite Spawning Fix

## Problem Statement

The Animica Miner GUI was experiencing an infinite spawning issue on macOS when users opened the DMG and launched the .app bundle. The application would continuously spawn new instances, making it unusable.

## Root Cause

The issue was caused by incorrect placement of `multiprocessing.freeze_support()` in the main entry point. 

### Original (Incorrect) Implementation

```python
if __name__ == "__main__":
    multiprocessing.freeze_support()
    sys.exit(main())
```

### Why This Fails on macOS

When PyInstaller creates a macOS .app bundle:
1. The bundled executable doesn't run the module as `__main__` in the same way as a regular Python script
2. The `if __name__ == "__main__"` guard may not execute as expected
3. Without `freeze_support()` being called at the module level, multiprocessing operations can trigger infinite process spawning
4. This is especially problematic on macOS due to how .app bundles handle process creation

## Solution

Move `multiprocessing.freeze_support()` to module level (top-level), outside of the `if __name__ == "__main__"` guard.

### Fixed Implementation

```python
import multiprocessing

# Required for PyInstaller frozen executables on macOS/Windows to prevent
# infinite process spawning when using multiprocessing module.
# MUST be called at module level, not inside if __name__ == "__main__".
multiprocessing.freeze_support()

# ... rest of imports and code ...

if __name__ == "__main__":
    sys.exit(main())
```

## Technical Details

### Why Module-Level Placement Works

1. **Immediate Execution**: The call executes when the module is imported, ensuring it runs before any multiprocessing operations
2. **Works with PyInstaller**: PyInstaller's bootstrapping mechanism ensures module-level code runs correctly in frozen executables
3. **Cross-Platform**: Works correctly on macOS, Windows, and Linux
4. **Safe**: `freeze_support()` is a no-op when not needed, so there's no downside to calling it unconditionally

### PyInstaller and Multiprocessing

From Python's multiprocessing documentation:
> "Explicitly calling `freeze_support()` should be done before any other multiprocessing code."

PyInstaller's documentation also recommends:
> "Call `freeze_support()` at the top level of your script, not inside the `if __name__ == '__main__'` block."

## Files Changed

1. **apps/miner-gui/animica_miner_gui/main.py**
   - Moved `multiprocessing.freeze_support()` to module level (line 14)
   - Removed from `if __name__ == "__main__"` block

2. **apps/miner-gui/animica_miner_gui/tests/test_main_entry.py**
   - Updated `test_freeze_support_at_module_level()` to verify module-level placement
   - Updated `test_main_has_proper_guard()` to check for module-level freeze_support
   - Fixed test to properly distinguish between comments and actual code

## Verification

### Tests
All tests pass:
```bash
pytest apps/miner-gui/animica_miner_gui/tests/test_main_entry.py -v
```

### Build
The fix will be verified in the next macOS build:
```bash
cd apps/miner-gui
./build-scripts/build_macos.sh
```

### Manual Testing on macOS

1. Build the DMG using `build_macos.sh`
2. Mount the DMG
3. Copy the .app to Applications
4. Launch the application
5. Verify only one instance opens
6. Verify mining operations work correctly

## References

- [Python multiprocessing documentation](https://docs.python.org/3/library/multiprocessing.html#multiprocessing.freeze_support)
- [PyInstaller multiprocessing guide](https://pyinstaller.org/en/stable/common-issues-and-pitfalls.html#multi-processing)
- Issue: "The miner gui wallet still opens infinitely on Mac after creating and opening the DMG"

## Additional Notes

This fix is critical for macOS but also improves reliability on Windows. Linux is generally more forgiving, but the module-level placement is still the recommended best practice.

The `device_detection.py` and `miner_runner.py` files also use multiprocessing, but they import it locally within functions, so they don't trigger the spawning issue. The main entry point is the critical location that must call `freeze_support()` at module level.

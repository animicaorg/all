# Segmentation Fault Fix - Logs Page

## Problem

The miner GUI was experiencing segmentation faults when navigating to the Logs page. This was caused by a violation of Qt's thread-safety requirements.

## Root Cause

The mining events were being emitted from a background thread (`MinerRunner._run_miner_thread()`) and directly calling UI update methods on the tabs (LogsTab, DashboardTab, StatsTab). In Qt, all UI operations **must** happen in the main thread. When background threads directly modify Qt widgets, it can cause:

- Segmentation faults
- Crashes
- Undefined behavior
- Data corruption

## Solution

Implemented Qt's signal/slot mechanism to ensure thread-safe event handling:

### Before (Thread-Unsafe)
```python
def on_mining_event(self, event: MiningEvent) -> None:
    # This is called from background thread
    # Directly updates UI widgets - NOT SAFE!
    self.log_display.setPlainText(...)  # CRASHES
```

### After (Thread-Safe)
```python
# Define a signal at class level
mining_event_received = Signal(object)  # MiningEvent

def __init__(self, parent=None):
    super().__init__(parent)
    # Connect signal to slot
    self.mining_event_received.connect(self._handle_mining_event_in_main_thread)

def on_mining_event(self, event: MiningEvent) -> None:
    # Can be called from any thread
    # Just emits a signal - SAFE!
    self.mining_event_received.emit(event)

@Slot(object)
def _handle_mining_event_in_main_thread(self, event: MiningEvent) -> None:
    # This slot is always called in the main thread
    # Safe to update UI widgets here
    self.log_display.setPlainText(...)  # SAFE
```

## How It Works

1. **Background Thread**: Mining runner emits events from its thread
2. **Signal Emission**: `on_mining_event()` emits a Qt signal (thread-safe operation)
3. **Qt Event Loop**: Qt's event loop marshals the signal to the main thread
4. **Slot Execution**: `_handle_mining_event_in_main_thread()` is called in the main thread
5. **UI Update**: Now safe to update Qt widgets

## Files Modified

- `apps/miner-gui/animica_miner_gui/ui/tabs/logs.py`
- `apps/miner-gui/animica_miner_gui/ui/tabs/dashboard.py`
- `apps/miner-gui/animica_miner_gui/ui/tabs/stats.py`

## Testing

Created test file to verify the signal/slot pattern:
- `apps/miner-gui/animica_miner_gui/tests/test_logs_tab_threading.py`

The tests verify that:
- Each tab has the `mining_event_received` signal
- Each tab has the `_handle_mining_event_in_main_thread` slot
- The structure follows Qt's thread-safety requirements

## References

- [Qt Thread-Safety Documentation](https://doc.qt.io/qt-6/threads-qobject.html)
- [Qt Signals and Slots](https://doc.qt.io/qt-6/signalsandslots.html)
- [PySide6 Threading Guide](https://doc.qt.io/qtforpython-6/overviews/threads-technologies.html)

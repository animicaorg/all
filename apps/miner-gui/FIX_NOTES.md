# Fix Notes for ConfigurationTab AttributeError

## Issue
When launching the miner-gui application, an `AttributeError` occurred:
```
AttributeError: 'ConfigurationTab' object has no attribute 'status_label'
```

## Root Cause
In `apps/miner-gui/animica_miner_gui/ui/tabs/configuration.py`, the `setup_ui()` method was calling `load_config_to_editor()` before creating the `status_label` widget.

### Before Fix
```python
def setup_ui(self) -> None:
    # ...
    self.editor = QTextEdit()
    self.editor.setFontFamily("monospace")
    self.load_config_to_editor()  # ← Called here at line 48
    layout.addWidget(self.editor)
    
    # ... more UI setup ...
    
    # Status label
    self.status_label = QLabel("")  # ← Created here at line 70
    layout.addWidget(self.status_label)
    
    self.setLayout(layout)
```

The `load_config_to_editor()` method tries to use `self.status_label` to display status messages, but it was not yet created.

## Solution
Moved the call to `load_config_to_editor()` to the end of `setup_ui()`, after all UI elements (including `status_label`) are created.

### After Fix
```python
def setup_ui(self) -> None:
    # ...
    self.editor = QTextEdit()
    self.editor.setFontFamily("monospace")
    layout.addWidget(self.editor)  # ← Removed the early call
    
    # ... more UI setup ...
    
    # Status label
    self.status_label = QLabel("")
    layout.addWidget(self.status_label)
    
    self.setLayout(layout)
    
    # Load config after UI is fully set up
    self.load_config_to_editor()  # ← Moved to the end
```

## Verification
- Static analysis confirmed no other tab files have similar issues
- Syntax validation passed
- Order of operations verified: `status_label` is now created before `load_config_to_editor()` is called

## Files Changed
- `apps/miner-gui/animica_miner_gui/ui/tabs/configuration.py`

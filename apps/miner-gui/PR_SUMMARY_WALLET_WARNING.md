# PR Summary: Add Wallet Location Warning to Miner Wizard

## Problem Statement
Users needed a clear warning in the Animica GUI wallet miner wizard stating that their wallets.json file must be located at ~/.animica/wallets.json.

## Solution
Added a prominent, styled warning message to the WalletConfigPage (Step 3) of the setup wizard that informs users about the default wallet file location and the option to browse for custom locations.

## Changes Made

### 1. Core Implementation (wizard.py)
**File**: `apps/miner-gui/animica_miner_gui/ui/wizard.py`

Added a warning label to the `WalletConfigPage.__init__()` method:
- Orange color scheme (#ff9800) for visibility
- Semi-transparent background (rgba(255, 152, 0, 0.1))
- 3px left border for emphasis
- Warning emoji (⚠️) for immediate recognition
- Word-wrapped text for readability
- Positioned between action buttons and validation label

```python
warning_label = QLabel(
    "⚠️ Note: When importing from wallets, your wallets.json file must be located at ~/.animica/wallets.json\n"
    "or you can browse to select a custom wallet file location."
)
warning_label.setWordWrap(True)
warning_label.setStyleSheet(
    "color: #ff9800; "
    "background-color: rgba(255, 152, 0, 0.1); "
    "padding: 8px; "
    "border-left: 3px solid #ff9800; "
    "border-radius: 3px; "
    "margin-top: 8px; "
    "margin-bottom: 8px;"
)
layout.addWidget(warning_label)
```

### 2. Verification Script Update
**File**: `apps/miner-gui/verify_wallet_features.py`

Enhanced the `verify_wallet_config_page()` function to check for the warning label:
- Iterates through page layout widgets
- Looks for QLabel containing the warning text
- Verifies presence of emoji, "wallets.json", and the default path

### 3. Documentation Updates

#### README.md
Added note in the "Payout Address" section explaining the warning and its purpose.

#### WALLET_LOCATION_WARNING.md (New)
Comprehensive feature documentation including:
- Visual mockup in ASCII art
- Style details and specifications
- Before/after user experience comparison
- Technical implementation details
- Testing instructions
- Benefits and future enhancements

#### WALLET_WARNING_VISUAL.txt (New)
ASCII art representation of the wizard page showing:
- Exact placement of the warning box
- Visual hierarchy
- Before/after comparison
- Key benefits

#### wallet_warning_mockup.html (New)
Interactive HTML mockup that can be viewed in a browser to see:
- Exact styling and appearance
- Color scheme and spacing
- Text layout and formatting

## Testing

### Automated Testing
- Python syntax validation: ✓ Passed
- Import checks: ✓ Passed
- Verification script updated to check for warning

### Manual Testing
To test manually:
1. Run: `animica gui miner` or `animica-miner-gui`
2. Navigate to Step 3 (Payout Address)
3. Verify warning box appears with orange styling
4. Verify text is clear and readable
5. Test that warning doesn't interfere with workflow

## Files Changed
```
apps/miner-gui/README.md                      |   2 +
apps/miner-gui/WALLET_LOCATION_WARNING.md     | 137 +++++++++++++++++++
apps/miner-gui/WALLET_WARNING_VISUAL.txt      |  83 +++++++++++
apps/miner-gui/animica_miner_gui/ui/wizard.py |  17 +++
apps/miner-gui/verify_wallet_features.py      |  19 +++
apps/miner-gui/wallet_warning_mockup.html     | 130 ++++++++++++++++
6 files changed, 388 insertions(+)
```

## User Impact

### Before
Users might be confused about wallet file location when importing wallets, leading to:
- Failed imports
- Support requests
- Frustration with unclear error messages

### After
Users are proactively informed about:
- Default wallet file location (~/.animica/wallets.json)
- Option to browse for custom locations
- When this information is relevant (during import)

## Visual Representation

```
┌─────────────────────────────────────────────────────┐
│  [Create New Wallet]  [Import from Wallets]        │
│                                                     │
│  ┌───────────────────────────────────────────────┐ │
│  │ ⚠️  Note: When importing from wallets, your   │ │
│  │ wallets.json file must be located at         │ │
│  │ ~/.animica/wallets.json or you can browse    │ │
│  │ to select a custom wallet file location.     │ │
│  └───────────────────────────────────────────────┘ │
│            ↑ NEW WARNING BOX                        │
└─────────────────────────────────────────────────────┘
```

## Design Decisions

### Why Orange Color?
- Standard UI convention for informational warnings
- Not as severe as red (error) or yellow (caution)
- Stands out without being alarming

### Why This Location?
- Appears after action buttons but before validation
- Users see it before attempting import
- Doesn't block the workflow
- Natural reading order

### Why This Wording?
- Starts with warning emoji for immediate attention
- States the requirement clearly
- Provides the default path explicitly
- Reminds about custom location option
- Concise but complete

## Code Review Feedback

The code review suggested extracting `~/.animica/wallets.json` to a constant. This was considered but kept as-is because:
1. It's user-facing text (not code logic)
2. Tilde notation is standard for Unix paths
3. More familiar to users than programmatic constants
4. The actual code logic uses `Path.home() / ".animica" / "wallets.json"`

## Future Enhancements

Potential improvements for future versions:
1. Add a "Learn More" link to wallet documentation
2. Show platform-specific path examples
3. Highlight the path when wallet import fails
4. Add tooltips with additional context

## Related Features

This warning complements:
- Wallet creation with custom locations
- Wallet import functionality
- Configuration persistence
- Error handling for missing wallets

## Benefits

✓ Improved user experience
✓ Reduced confusion and support requests
✓ Better discoverability of custom locations
✓ Professional, polished appearance
✓ Follows UI best practices
✓ Non-intrusive but clearly visible
✓ Consistent with existing design language

## Commits

1. `f59742f5` - Add wallet location warning to miner wizard
2. `06c5bac4` - Update verification script and documentation for wallet warning
3. `a29630a7` - Add visual documentation and mockup for wallet warning

## Conclusion

Successfully implemented a clear, prominent warning about wallet file location in the miner wizard. The change is minimal (17 lines of code), well-documented, and provides significant UX improvement for users setting up the Animica GUI miner.

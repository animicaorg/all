# Wallet Location Warning Feature

## Summary

Added a visual warning message in the Animica GUI miner wizard's wallet configuration page to inform users about the default location of the `wallets.json` file.

## Visual Change

### Location
The warning appears in the **Wallet Configuration Page** (Step 3 of the setup wizard), between the action buttons and the validation status label.

### Appearance
```
┌─────────────────────────────────────────────────────────────┐
│  Payout Address                                              │
│  Configure your wallet address for receiving mining rewards  │
├─────────────────────────────────────────────────────────────┤
│                                                              │
│  Enter payout address:                                       │
│  ┌─────────────────────────────────────────────────────┐   │
│  │ anim1...                                             │   │
│  └─────────────────────────────────────────────────────┘   │
│                                                              │
│  [Create New Wallet]  [Import from Wallets]                 │
│                                                              │
│  ┌─────────────────────────────────────────────────────┐   │
│  │ ⚠️ Note: When importing from wallets, your          │   │
│  │ wallets.json file must be located at                │   │
│  │ ~/.animica/wallets.json or you can browse to        │   │
│  │ select a custom wallet file location.               │   │
│  └─────────────────────────────────────────────────────┘   │
│                                                              │
│  [Validation status appears here]                           │
│                                                              │
└─────────────────────────────────────────────────────────────┘
```

### Style Details
- **Color**: Orange warning color (#ff9800)
- **Background**: Light orange with transparency (rgba(255, 152, 0, 0.1))
- **Border**: 3px solid orange border on the left side
- **Padding**: 8px internal spacing
- **Border Radius**: 3px rounded corners
- **Text**: Word-wrapped for readability
- **Icon**: Warning emoji (⚠️) at the start

## User Impact

### Before
Users were not explicitly informed about the default wallet file location when importing wallets, which could lead to confusion if their wallet file was not in the expected location.

### After
Users now see a clear, prominent warning that:
1. Informs them of the default wallet location (`~/.animica/wallets.json`)
2. Reminds them they can browse to select a custom location
3. Provides context about when this applies (when importing from wallets)

## Technical Implementation

### File Modified
- `apps/miner-gui/animica_miner_gui/ui/wizard.py`

### Changes Made
Added a `QLabel` widget with:
- Warning icon and informative text
- Custom styling for visibility
- Word wrapping enabled for long text
- Positioned between buttons and validation label

### Code Snippet
```python
# Warning about wallet location
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

## Related Features

This warning complements existing wallet management features:

1. **Create New Wallet**: Users can create wallets with custom locations
2. **Import from Wallets**: Users can browse to select wallet files
3. **Default Location**: `~/.animica/wallets.json` is still the default
4. **Custom Locations**: Full support for custom wallet file paths

## Testing

### Automated Testing
The verification script (`verify_wallet_features.py`) has been updated to check for the presence of the warning label.

### Manual Testing
1. Run the miner GUI: `animica gui miner` or `animica-miner-gui`
2. Navigate through the setup wizard
3. On the "Payout Address" page, observe the warning message
4. Verify the warning is clearly visible and readable
5. Check that the styling makes it stand out without being intrusive

## Screenshots

To verify the implementation visually:
1. Launch the GUI with `animica-miner-gui`
2. Step through to the Wallet Configuration page (Step 3)
3. The warning should appear as an orange-highlighted box below the buttons

## Benefits

1. **Improved UX**: Users are explicitly informed about wallet file requirements
2. **Reduced Confusion**: Clearer guidance prevents wallet import issues
3. **Better Discoverability**: Users learn about custom location options
4. **Professional Appearance**: Styled warning box follows UI best practices

## Future Enhancements

Potential improvements:
1. Add a direct link to documentation about wallet management
2. Include a "Learn More" button with detailed wallet setup instructions
3. Show example paths for different operating systems
4. Add tooltips with additional context

## Related Documentation

- `README.md` - Updated with wallet location note
- `WALLET_FILE_LOCATION_FEATURE.md` - Custom wallet location feature
- `verify_wallet_features.py` - Updated verification script

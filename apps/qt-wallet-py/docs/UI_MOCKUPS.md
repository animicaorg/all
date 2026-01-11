# Wallet UI Screenshots and Mockups

This document provides visual mockups of the Send and Receive tabs implemented in the Animica Qt Wallet.

## Send Tab

### Main View
```
┌─────────────────────────────────────────────────────────────┐
│ Send ANM                                                     │
│ Send ANM tokens to another address.                         │
│                                                              │
│ ┌─ Transaction Details ─────────────────────────────────┐  │
│ │                                                         │  │
│ │ From:     [Account 1 (anim1qyfe...xw8z)        ▼]     │  │
│ │                                                         │  │
│ │ To:       [anim1...                            ]       │  │
│ │                                                         │  │
│ │ Amount:   [0.0                                 ] ANM   │  │
│ │                                                         │  │
│ └─────────────────────────────────────────────────────────┘  │
│                                                              │
│ ☐ Advanced Options                                          │
│                                                              │
│                                                              │
│                                                              │
│                                             [   Send   ]     │
└─────────────────────────────────────────────────────────────┘
```

### With Advanced Options Expanded
```
┌─────────────────────────────────────────────────────────────┐
│ Send ANM                                                     │
│ Send ANM tokens to another address.                         │
│                                                              │
│ ┌─ Transaction Details ─────────────────────────────────┐  │
│ │ From:     [Account 1 (anim1qyfe...xw8z)        ▼]     │  │
│ │ To:       [anim1abc...def                      ]       │  │
│ │ Amount:   [1.5                                 ] ANM   │  │
│ └─────────────────────────────────────────────────────────┘  │
│                                                              │
│ ☑ Advanced Options                                          │
│                                                              │
│ ┌─ Advanced ────────────────────────────────────────────┐  │
│ │                                                         │  │
│ │ Gas Limit:         [21000                      ]       │  │
│ │                                                         │  │
│ │ Max Fee (nANM):    [1000000000                 ]       │  │
│ │                                                         │  │
│ │ Nonce (optional):  [Auto                       ]       │  │
│ │                                                         │  │
│ │                    [ Estimate Fees ]                   │  │
│ │                                                         │  │
│ │                    Estimated fee: ~0.000021000 ANM     │  │
│ │                    (max_fee: 1000000000 nANM/gas)      │  │
│ └─────────────────────────────────────────────────────────┘  │
│                                                              │
│                                             [   Send   ]     │
└─────────────────────────────────────────────────────────────┘
```

### Confirmation Dialog
```
┌─ Confirm Transaction ──────────────────────────────────────┐
│                                                             │
│  ⚠️  Please review the transaction details carefully      │
│     before sending.                                        │
│                                                             │
│  ┌─ Transaction Details ────────────────────────────────┐ │
│  │                                                       │ │
│  │  From:           anim1qyfe...xw8z                    │ │
│  │  To:             anim1abc...def                      │ │
│  │  Amount:         1.500000000 ANM                     │ │
│  │  Gas Limit:      21000                               │ │
│  │  Max Fee:        1000000000 nANM/gas                 │ │
│  │  Max Total Cost: 1.500021000 ANM (i)                │ │
│  │  Nonce:          5                                   │ │
│  │  Chain ID:       1                                   │ │
│  │                                                       │ │
│  └───────────────────────────────────────────────────────┘ │
│                                                             │
│                              [  Cancel  ]  [    OK    ]     │
└─────────────────────────────────────────────────────────────┘

(i) = Tooltip: "Maximum possible cost including transfer and gas. 
                 Actual cost will likely be lower."
```

### Success Dialog
```
┌─ Transaction Sent ──────────────────────────────────────────┐
│                                                              │
│  ✅ Transaction sent successfully!                          │
│                                                              │
│  Transaction Hash:                                          │
│  ┌────────────────────────────────────────────────────────┐ │
│  │ 0x1234567890abcdef1234567890abcdef1234567890abcdef... │ │
│  │ ...1234567890abcdef                                   │ │
│  └────────────────────────────────────────────────────────┘ │
│                                                              │
│  [      Copy Hash      ]                                     │
│                                                              │
│  [       Close        ]                                      │
└──────────────────────────────────────────────────────────────┘
```

### Error Example
```
┌─────────────────────────────────────────────────────────────┐
│ Send ANM                                                     │
│                                                              │
│ ... (form fields) ...                                        │
│                                                              │
│ Error: Insufficient balance to complete this transaction.   │
│                                                              │
│                                             [   Send   ]     │
└─────────────────────────────────────────────────────────────┘
```

## Receive Tab

### Main View
```
┌─────────────────────────────────────────────────────────────┐
│ Receive ANM                                                  │
│ Share your address to receive ANM tokens.                   │
│                                                              │
│ Account:  [Account 1 (anim1qyfe...xw8z)          ▼]        │
│                                                              │
│ ┌─ Your Address ───────────────────────────────────────┐   │
│ │                                                       │   │
│ │  anim1qyfe4567890abcdef1234567890abcdef1234567xw8z  │   │
│ │                                                       │   │
│ │                               [  Copy Address  ]     │   │
│ └───────────────────────────────────────────────────────┘   │
│                                                              │
│ ┌─ QR Code ────────────────────────────────────────────┐   │
│ │                                                       │   │
│ │              ┌─────────────────┐                     │   │
│ │              │█▀▀▀▀▀█ ▄█ █▀▀▀▀█│                     │   │
│ │              │█ ███ █ ▀█ █ ███ │                     │   │
│ │              │█ ▀▀▀ █ ▀▀ █ ▀▀▀ │                     │   │
│ │              │▀▀▀▀▀▀▀ ▀ ▀▀▀▀▀▀▀▀│                     │   │
│ │              │█▀█ █▀ ▀ ▀█ █▀ ▀█ │                     │   │
│ │              │  ▀ ▀▀▀█ ▀▀▀█  ▀▀ │                     │   │
│ │              │█▀▀▀▀▀█ ██ █▀█▀ ▀ │                     │   │
│ │              │█ ███ █ ▄▀ ██▀▀█▀ │                     │   │
│ │              │█ ▀▀▀ █ ▀▀█▀ ▀▀ ▀ │                     │   │
│ │              │▀▀▀▀▀▀▀ ▀▀▀ ▀▀ ▀▀▀│                     │   │
│ │              └─────────────────┘                     │   │
│ │                                                       │   │
│ └───────────────────────────────────────────────────────┘   │
│                                                              │
│ Scan the QR code or share the address above to receive     │
│ ANM tokens.                                                  │
│                                                              │
└─────────────────────────────────────────────────────────────┘
```

### After Clicking Copy
```
┌─────────────────────────────────────────────────────────────┐
│ Receive ANM                                                  │
│ Share your address to receive ANM tokens.                   │
│                                                              │
│ Account:  [Account 1 (anim1qyfe...xw8z)          ▼]        │
│                                                              │
│ ┌─ Your Address ───────────────────────────────────────┐   │
│ │                                                       │   │
│ │  anim1qyfe4567890abcdef1234567890abcdef1234567xw8z  │   │
│ │                                                       │   │
│ │                               [   ✓ Copied!    ]     │   │
│ └───────────────────────────────────────────────────────┘   │
│                                                              │
│ ... (QR code) ...                                            │
│                                                              │
└─────────────────────────────────────────────────────────────┘

(Button reverts to "Copy Address" after 2 seconds)
```

### Without QR Code Library
```
┌─ QR Code ────────────────────────────────────────────┐
│                                                       │
│          QR code generation unavailable               │
│   (install qrcode library: pip install qrcode[pil])  │
│                                                       │
└───────────────────────────────────────────────────────┘
```

## Tab Navigation

The wallet has the following tab structure:

```
┌─────────────────────────────────────────────────────────────┐
│ Animica Wallet                                               │
│                                                              │
│ ┌─ Accounts ───────────────────────────────────────────┐   │
│ │ Wallet: Unlocked                 [Lock] [Unlock]     │   │
│ │ ┌────────┬───────────────────────────────────────┐   │   │
│ │ │ Label  │ Address                               │   │   │
│ │ ├────────┼───────────────────────────────────────┤   │   │
│ │ │Account1│anim1qyfe4567890abcdef...             │   │   │
│ │ └────────┴───────────────────────────────────────┘   │   │
│ │ [Create Account] [Import Account] [Show Secret]      │   │
│ └──────────────────────────────────────────────────────┘   │
│                                                              │
│ ┌────────────────────────────────────────────────────────┐ │
│ │ Overview │ Send │ Receive │ Node │                     │ │
│ ├────────────────────────────────────────────────────────┤ │
│ │                                                        │ │
│ │  (Tab content shown above)                            │ │
│ │                                                        │ │
│ └────────────────────────────────────────────────────────┘ │
│                                                              │
│ Node: running (mainnet)     Walletd: OK                     │
└─────────────────────────────────────────────────────────────┘
```

## Color Scheme

- **Success messages**: Green (#4caf50)
- **Error messages**: Red (#b00020)
- **Warning messages**: Orange (#ff9800)
- **Info/status messages**: Gray (#666)
- **Backgrounds**: Light gray (#f5f5f5)
- **Borders**: Light gray (#ddd)

## Responsive Behavior

- Window minimum size: 900x600
- All text fields expand to fill available width
- QR code maintains square aspect ratio (240x240)
- Scrollable content areas where needed
- Modal dialogs center on parent window

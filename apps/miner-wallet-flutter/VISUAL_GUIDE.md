# Visual Guide - Flutter Miner-Wallet UI

## Application Layout

### Main Window Structure
```
┌─────────────────────────────────────────────────────────────┐
│  Animica Miner-Wallet                                   ⚙️ │
├────┬────────────────────────────────────────────────────────┤
│ 📊 │                                                        │
│Mining│                  DASHBOARD VIEW                      │
│    │                                                        │
├────┤  [Chain Status Card]                                  │
│ 💰 │  Chain ID: 1337                                       │
│Wallet│  Block Height: 12,345                               │
│    │  Sync Status: Synced                                  │
├────┤                                                        │
│ ⚙️ │  [Mining Status Card]                                 │
│Settings│  Status: Running                                  │
│    │  Hashrate: 125.5 MH/s                                │
│    │  Difficulty: 1,234,567                                │
│    │  Time to Block: ~2.5h                                │
│    │  Blocks Found: 42                                     │
│    │                                                        │
│    │  [▶ Start Mining]  [⏸ Stop]                          │
│    │                                                        │
└────┴────────────────────────────────────────────────────────┘
```

## Page Designs

### 1. Dashboard Page (Mining)

**Layout:**
```
┌─────────────────────────────────────────────────────────┐
│ Mining Dashboard                                        │
├─────────────────────────────────────────────────────────┤
│                                                         │
│ ┌─────────────────────────────────────────────────┐   │
│ │ Chain Status                                    │   │
│ │                                                 │   │
│ │ Chain ID:          1337                        │   │
│ │ Block Height:      12,345                      │   │
│ │ Sync Status:       Synced                      │   │
│ └─────────────────────────────────────────────────┘   │
│                                                         │
│ ┌─────────────────────────────────────────────────┐   │
│ │ Mining Status                                   │   │
│ │                                                 │   │
│ │ Status:            Running                      │   │
│ │ Hashrate:          125.5 MH/s                  │   │
│ │ Difficulty:        1,234,567                    │   │
│ │ Time to Block:     ~2.5h                       │   │
│ │ Blocks Found:      42                          │   │
│ │                                                 │   │
│ │ [▶ Start Mining]  [⏸ Stop]                    │   │
│ └─────────────────────────────────────────────────┘   │
│                                                         │
└─────────────────────────────────────────────────────────┘
```

**Colors:**
- Background: `#0B0D12` (dark)
- Cards: `#1A1D24` (surface)
- Primary: `#5EEAD4` (teal)
- Text: `#F9FAFB` (light)
- Hashrate: Large, bold, teal color

### 2. Wallet Page

**Layout:**
```
┌─────────────────────────────────────────────────────────┐
│ Wallet                                                  │
├─────────────────────────────────────────────────────────┤
│                                                         │
│ ┌─────────────────────────────────────────────────┐   │
│ │ Wallet Information                              │   │
│ │                                                 │   │
│ │ Address                          [📋 Copy]     │   │
│ │ anim1qw...xyz123                               │   │
│ │ ─────────────────────────────────────────      │   │
│ │ Balance                          [🔄 Refresh]  │   │
│ │ 1,234.567890 ANM                               │   │
│ │                                                 │   │
│ │ Nonce:                           42            │   │
│ └─────────────────────────────────────────────────┘   │
│                                                         │
│ ┌─────────────────────────────────────────────────┐   │
│ │ Send Transaction                                │   │
│ │                                                 │   │
│ │ To Address                                      │   │
│ │ [anim1...                                    ] │   │
│ │                                                 │   │
│ │ Amount (ANM)                                    │   │
│ │ [0.0                                         ] │   │
│ │                                                 │   │
│ │ [📤 Send]                                      │   │
│ └─────────────────────────────────────────────────┘   │
│                                                         │
└─────────────────────────────────────────────────────────┘
```

**Features:**
- Address truncated with "..." for readability
- Copy button with clipboard icon
- Refresh button for balance
- Large balance display in bold
- Send form with validation

### 3. Settings Page

**Layout:**
```
┌─────────────────────────────────────────────────────────┐
│ Settings                                                │
├─────────────────────────────────────────────────────────┤
│                                                         │
│ [🌐] Network                                      >     │
│      RPC URL and Chain ID                              │
│ ─────────────────────────────────────────────────      │
│ [⚙️] Mining Configuration                         >     │
│      Device settings and performance                   │
│ ─────────────────────────────────────────────────      │
│ [🏊] Pool Settings                                 >     │
│      Configure mining pool                             │
│ ─────────────────────────────────────────────────      │
│ [</>] JSON Configuration                           >     │
│      Edit raw config                                   │
│ ─────────────────────────────────────────────────      │
│ [📋] Logs                                          >     │
│      View mining logs                                  │
│ ─────────────────────────────────────────────────      │
│ [📊] Statistics                                    >     │
│      Hashrate graphs and stats                         │
│ ─────────────────────────────────────────────────      │
│ [📱] System Tray                           [ON/OFF]    │
│      Minimize to system tray                           │
│ ─────────────────────────────────────────────────      │
│ [🔔] Notifications                         [ON/OFF]    │
│      Show mining notifications                         │
│ ─────────────────────────────────────────────────      │
│ [ℹ️] About                                         >     │
│      Version 0.1.0                                     │
│                                                         │
└─────────────────────────────────────────────────────────┘
```

**Features:**
- List-based navigation
- Icons for each section
- Toggle switches for binary options
- Chevron (>) for navigation items
- Subtle dividers between items

### 4. Devices Page (TODO)

**Planned Layout:**
```
┌─────────────────────────────────────────────────────────┐
│ Devices                                                 │
├─────────────────────────────────────────────────────────┤
│                                                         │
│ ┌─────────────────────────────────────────────────┐   │
│ │ 💻 CPU                              [✓ Enabled] │   │
│ │                                                 │   │
│ │ Intel Core i7-9700K                             │   │
│ │ 8 cores, 8 threads available                    │   │
│ │                                                 │   │
│ │ Threads:  [━━━━━━━░░] 6                       │   │
│ │                                                 │   │
│ └─────────────────────────────────────────────────┘   │
│                                                         │
│ ┌─────────────────────────────────────────────────┐   │
│ │ 🎮 GPU #0                           [✓ Enabled] │   │
│ │                                                 │   │
│ │ NVIDIA RTX 3080                                 │   │
│ │ 8704 CUDA cores, 10GB VRAM                      │   │
│ │                                                 │   │
│ │ Intensity: [━━━━━━━━░] 8                       │   │
│ │                                                 │   │
│ └─────────────────────────────────────────────────┘   │
│                                                         │
│ ┌─────────────────────────────────────────────────┐   │
│ │ 🎮 GPU #1                           [  Disabled]│   │
│ │                                                 │   │
│ │ AMD Radeon RX 580                               │   │
│ │ 2304 stream processors, 8GB VRAM                │   │
│ │                                                 │   │
│ └─────────────────────────────────────────────────┘   │
│                                                         │
└─────────────────────────────────────────────────────────┘
```

### 5. Logs Page (TODO)

**Planned Layout:**
```
┌─────────────────────────────────────────────────────────┐
│ Logs                    [🔍] [Export] [Level: INFO ▼]  │
├─────────────────────────────────────────────────────────┤
│                                                         │
│ 2024-01-06 20:15:32 [INFO   ] Miner: Starting...      │
│ 2024-01-06 20:15:32 [INFO   ] Miner: Loading config   │
│ 2024-01-06 20:15:33 [INFO   ] Miner: Connecting RPC   │
│ 2024-01-06 20:15:33 [INFO   ] Miner: Connected        │
│ 2024-01-06 20:15:34 [INFO   ] Miner: Mining started   │
│ 2024-01-06 20:15:35 [DEBUG  ] Miner: Template update  │
│ 2024-01-06 20:15:40 [INFO   ] Miner: Hashrate 125 MH/s│
│ 2024-01-06 20:15:45 [SUCCESS] Miner: Block found!     │
│ 2024-01-06 20:15:45 [INFO   ] Miner: Height 12346     │
│ 2024-01-06 20:15:50 [INFO   ] Miner: Hashrate 127 MH/s│
│ ...                                                     │
│                                                         │
│                                    [Auto-scroll: ON]   │
└─────────────────────────────────────────────────────────┘
```

### 6. Stats Page (TODO)

**Planned Layout:**
```
┌─────────────────────────────────────────────────────────┐
│ Statistics              [1H] [6H] [24H] [7D] [30D]     │
├─────────────────────────────────────────────────────────┤
│                                                         │
│ ┌─────────────────────────────────────────────────┐   │
│ │ Hashrate Over Time                              │   │
│ │                                                 │   │
│ │ 150 MH/s ┤              ╭─────╮                 │   │
│ │          │         ╭────╯     ╰─────╮           │   │
│ │ 100 MH/s ┤    ╭────╯                ╰───────    │   │
│ │          │ ╭──╯                              ╰─ │   │
│ │  50 MH/s ┤─╯                                    │   │
│ │          │                                      │   │
│ │   0 MH/s └──────────────────────────────────── │   │
│ │          0h      6h      12h     18h     24h   │   │
│ └─────────────────────────────────────────────────┘   │
│                                                         │
│ ┌──────────────────┐ ┌──────────────────┐            │
│ │ Average Hashrate │ │ Peak Hashrate    │            │
│ │ 125.3 MH/s      │ │ 152.7 MH/s      │            │
│ └──────────────────┘ └──────────────────┘            │
│                                                         │
│ ┌──────────────────┐ ┌──────────────────┐            │
│ │ Blocks Found     │ │ Uptime           │            │
│ │ 42              │ │ 23h 45m         │            │
│ └──────────────────┘ └──────────────────┘            │
│                                                         │
└─────────────────────────────────────────────────────────┘
```

## Color Palette

### Primary Colors
```
Primary (Teal):    #5EEAD4  ████ Buttons, highlights
Secondary (Indigo): #818CF8  ████ Accents
Background (Dark):  #0B0D12  ████ Main background
Surface (Card):     #1A1D24  ████ Cards, elevated
```

### Semantic Colors
```
Success (Green):   #34D399  ████ Block found, success
Warning (Yellow):  #FBBF24  ████ Warnings
Error (Red):       #F87171  ████ Errors
Info (Blue):       #60A5FA  ████ Information
```

### Text Colors
```
Primary Text:      #F9FAFB  ████ Main text
Secondary Text:    #9CA3AF  ████ Labels, hints
Disabled:          #6B7280  ████ Disabled text
```

## Typography

**Font Family:** Inter (Variable)

**Sizes:**
- Display Large: 32px, Bold - Page titles
- Display Medium: 28px, Bold - Section headers
- Headline: 20px, SemiBold - Card titles
- Body Large: 16px, Regular - Main content
- Body Medium: 14px, Regular - Secondary content
- Body Small: 12px, Regular - Labels, hints

## Navigation

**Desktop Layout:**
```
┌──────┬────────────────────────────────┐
│      │                                │
│ 📊   │        Page Content            │
│Mining│                                │
│      │                                │
│ 💰   │                                │
│Wallet│                                │
│      │                                │
│ ⚙️   │                                │
│Settings                               │
│      │                                │
└──────┴────────────────────────────────┘
```

**Mobile Layout:**
```
┌────────────────────────────────────────┐
│        Page Content                    │
│                                        │
│                                        │
│                                        │
└────────────────────────────────────────┘
  [📊 Mining] [💰 Wallet] [⚙️ Settings]
```

## Interactions

### Buttons
- **Elevated (Primary)**: Teal background, black text
- **Outlined (Secondary)**: Teal border, teal text
- **Text**: Teal text only

### Hover States
- Cards: Slight elevation increase
- Buttons: Brightness increase
- List items: Background highlight

### Loading States
- Skeleton screens for initial load
- Circular progress indicators for actions
- Linear progress for operations

### Animations
- Smooth page transitions (300ms)
- Card entrance animations (fade + slide)
- Button ripple effects (Material)
- Chart data updates (animated)

## Responsive Design

**Breakpoints:**
- Mobile: < 600px (single column)
- Tablet: 600-900px (single column, larger cards)
- Desktop: > 900px (navigation rail + content)

**Adaptations:**
- Navigation: Bottom bar (mobile) → Rail (desktop)
- Cards: Full width (mobile) → Max width (desktop)
- Forms: Vertical (mobile) → Horizontal (desktop)

## Accessibility

- High contrast mode support
- Screen reader labels
- Keyboard navigation
- Focus indicators
- Minimum touch target size (48x48)
- Semantic HTML (web)

## Platform Differences

**Desktop:**
- System tray icon
- Window controls
- Keyboard shortcuts
- Hover states

**Mobile:**
- Bottom navigation
- Pull to refresh
- Swipe gestures
- Haptic feedback

**Web:**
- Responsive layout
- Browser controls
- Copy/paste support
- Download/upload files

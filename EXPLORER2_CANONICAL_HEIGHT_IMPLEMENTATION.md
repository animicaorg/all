# Explorer2 Canonical Height Implementation

## Overview

This implementation adds differentiation between `height` and `canonicalHeight` in the explorer2 blockchain explorer to properly reflect the two types of blocks in the Animica blockchain:

- **Regular (mining) blocks**: Have `nonce > 0` and contribute to canonical height
- **Instant blocks**: Have `nonce = 0` and do NOT contribute to canonical height

The canonical height is used for block reward halving calculations and reflects only mining blocks.

## Changes Made

### 1. Backend Changes

#### RPC Layer (`rpc/deps.py`)
- Modified `_HeadAccessor.get()` to fetch and include `canonicalHeight` from `block_db.get_canonical_height()`
- Returns structure: `{"height": int, "canonicalHeight": int, "hash": str, "header": object}`

#### RPC Methods (`rpc/methods/chain.py`)
- Updated `chain_get_head()` to extract and include `canonicalHeight` from head snapshot
- Ensures RPC responses include both height values when available

#### Local Chain Client (`explorer2/api/src/localChainClient.ts`)
- Added `META_CANONICAL_HEIGHT` constant for database lookup
- Modified `getHead()` to read and return `canonicalHeight` from local DB

### 2. API Layer Changes

#### Shared Types (`explorer2/shared/src/types.ts`)
- `HeadView`: Added optional `canonicalHeight?: number`
- `BlockSummary`: Added optional `canonicalHeight?: number`
- `BlockDetail`: Added optional `canonicalHeight?: number` and `nonce?: number`

#### Normalization (`explorer2/api/src/normalize.ts`)
- `normalizeHead()`: Extracts `canonicalHeight` or `canonical_height` from raw data
- `normalizeBlockSummary()`: Extracts `canonicalHeight` from block/header
- `normalizeBlockDetail()`: Extracts both `canonicalHeight` and `nonce` from block/header

### 3. Frontend Changes

#### Home Page (`explorer2/web/src/pages/HomePage.tsx`)
- Displays canonical height below regular height when they differ
- Example display: `#12345` with subtitle "Canonical: #12340"

#### Blocks List (`explorer2/web/src/pages/BlocksPage.tsx`)
- Shows "instant" badge for blocks where `canonicalHeight ≠ height`
- Visual indicator helps identify instant blocks at a glance

#### Block Detail Page (`explorer2/web/src/pages/BlockDetailPage.tsx`)
- Shows "Instant Block" badge in header when `nonce === 0`
- Displays both height and canonical height in block info
- Shows nonce value with "(instant)" label when nonce is 0
- Provides complete information about block type

## Example Data Flow

### Mining Block
```json
{
  "height": 100,
  "canonicalHeight": 95,
  "nonce": 123456,
  "hash": "0xabc..."
}
```
Display: Block #100 (Canonical: #95)

### Instant Block
```json
{
  "height": 101,
  "canonicalHeight": 95,
  "nonce": 0,
  "hash": "0xdef..."
}
```
Display: Block #101 [Instant Block badge] (Canonical: #95)

## Visual Indicators

1. **Home Page**: Shows canonical height as subtitle when different from absolute height
2. **Blocks List**: Blue "instant" badge next to height for instant blocks
3. **Block Detail**: 
   - "Instant Block" badge in title
   - Height shows: "101 (canonical: 95)"
   - Nonce shows: "0 (instant)" in blue

## Testing

All tests pass including new test cases:
- ✅ Normalization of `canonicalHeight` field
- ✅ Support for both `canonicalHeight` and `canonical_height` variants
- ✅ Extraction of `nonce` field
- ✅ Detection of instant blocks (nonce=0)

Build verified:
- ✅ TypeScript compilation successful
- ✅ Shared package builds
- ✅ API package builds  
- ✅ Web package builds

## Benefits

1. **Transparency**: Users can see the actual mining activity vs total block count
2. **Accuracy**: Canonical height matches what's used for halving calculations
3. **Education**: Visual indicators help users understand instant vs mining blocks
4. **Compatibility**: Gracefully handles nodes that don't provide canonical height
5. **Consistency**: Follows the same naming conventions as the core blockchain

## Backward Compatibility

The implementation is fully backward compatible:
- All new fields are optional (`canonicalHeight?`, `nonce?`)
- Falls back gracefully when data is not available
- Existing functionality remains unchanged
- Only displays additional information when present

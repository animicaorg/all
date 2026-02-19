# ENA Image CLI Commands - Implementation Summary

## Overview

Implemented ENA image CLI commands in `python/animica/cli/ena.py` to handle image upload, retrieval, verification, and listing through the DA layer with automatic chunking support.

## Commands Implemented

### 1. `animica ena image put <file>`

Upload an image to DA with automatic chunking.

**Features:**
- Validates image format (PNG, JPG, JPEG, WEBP) via magic bytes
- Computes SHA-256 and SHA3-256 hashes
- Automatic chunking for files > 1MB (1MB chunks)
- Creates and uploads MediaManifest
- Stores metadata in local cache (~/.animica/media_cache.json)
- Extracts image dimensions (if PIL available)

**Options:**
- `--name <name>` - Display name for the image
- `--tag <tag>` - Add tag (can specify multiple times)
- `--from <wallet>` - Wallet identifier
- `--da-url <url>` - Custom DA service URL
- `--json` - Output as JSON

**Example:**
```bash
animica ena image put image.png --name "My Photo" --tag landscape --tag sunset
```

### 2. `animica ena image get <media_id>`

Retrieve an image by media_id.

**Features:**
- Downloads manifest from DA
- Retrieves and reassembles chunked content if needed
- Verifies content integrity against manifest hashes
- Saves to file with original name or custom output path

**Options:**
- `--output <path>` or `-o <path>` - Output file path
- `--da-url <url>` - Custom DA service URL
- `--json` - Output metadata as JSON

**Example:**
```bash
animica ena image get a1b2c3d4... --output downloaded.png
```

### 3. `animica ena image verify <media_id> <file>`

Verify an image matches the manifest.

**Features:**
- Downloads manifest from DA
- Computes local file hashes
- Compares SHA-256, SHA3-256, and file size
- Reports individual check results

**Options:**
- `--json` - Output as JSON

**Example:**
```bash
animica ena image verify a1b2c3d4... image.png
```

### 4. `animica ena image list`

List uploaded images from local cache.

**Features:**
- Displays all cached images in a table
- Shows media ID, name, size, type, dimensions, tags, creation date
- Filter by tag

**Options:**
- `--tag <tag>` - Filter by tag
- `--json` - Output as JSON

**Example:**
```bash
animica ena image list --tag landscape
```

## Technical Implementation

### Constants
- `ENA_MEDIA_NAMESPACE = 7` - Namespace for ENA media
- `CHUNK_SIZE = 1MB` - Size per chunk
- `CHUNK_THRESHOLD = 1MB` - Files larger than this are chunked

### Image Format Validation

**Supported formats:**
- PNG (magic bytes: `89 50 4E 47 0D 0A 1A 0A`)
- JPEG (magic bytes: `FF D8 FF`)
- WEBP (magic bytes: `RIFF....WEBP`)

**MIME type mappings:**
- `.png` → `image/png`
- `.jpg`, `.jpeg` → `image/jpeg`
- `.webp` → `image/webp`

### Helper Functions

1. **`_detect_image_format(file_path)`** - Detect format via magic bytes
2. **`_validate_image_file(file_path)`** - Validate format and return MIME type
3. **`_get_media_cache_path()`** - Get cache file path (~/.animica/media_cache.json)
4. **`_load_media_cache()`** - Load cache from disk
5. **`_save_media_cache(cache)`** - Save cache to disk
6. **`_add_to_cache(manifest_data)`** - Add manifest to cache
7. **`_get_image_dimensions(file_path, fmt)`** - Extract dimensions (requires PIL)

### Chunking Logic

Files larger than 1MB are automatically chunked:

1. Split file into 1MB chunks
2. Upload each chunk individually to DA namespace 7
3. Collect chunk commitments (32-byte hashes)
4. Create `ChunkingParams` with chunk metadata
5. Store in manifest as chunked content

On retrieval:
1. Download manifest
2. Check if content is `ChunkingParams` or single commitment
3. Download all chunks in order if chunked
4. Reassemble into original file
5. Verify integrity against manifest hashes

### MediaManifest Integration

Uses `da.media.manifest` module for:
- `MediaKind.IMAGE` - Image type identifier
- `create_manifest()` - Create manifest with hashes
- `verify_manifest()` - Verify content integrity
- `compute_hashes()` - Compute SHA-256 and SHA3-256
- CBOR serialization/deserialization

### DA Integration

Uses existing `da.cli.put_blob` and `da.cli.get_blob` utilities:
- Upload via subprocess call to `python -m da.cli.put_blob`
- Download via subprocess call to `python -m da.cli.get_blob`
- Namespace 7 for all ENA media uploads

### Local Cache

Cache stored at `~/.animica/media_cache.json`:

```json
{
  "images": [
    {
      "media_id": "a1b2c3d4...",
      "name": "image.png",
      "kind": "image",
      "content_type": "image/png",
      "byte_size": 1234567,
      "tags": ["landscape", "sunset"],
      "created_at": 1234567890,
      "width": 1920,
      "height": 1080,
      "manifest_commitment": "0xb1c2d3e4..."
    }
  ]
}
```

## File Structure

```
python/animica/cli/ena.py
├── Imports and setup
├── Existing ENA inference commands
├── AICF commands
├── Image commands (NEW)
│   ├── Constants (namespace, chunk size, formats)
│   ├── Helper functions (validation, cache, dimensions)
│   ├── @image_app.command("put")    - Upload image
│   ├── @image_app.command("get")    - Download image
│   ├── @image_app.command("verify") - Verify integrity
│   └── @image_app.command("list")   - List cached images
└── Main entry point
```

## Testing

Basic validation tests:
- ✓ PNG magic byte detection
- ✓ JPEG magic byte detection
- ✓ WEBP magic byte detection
- ✓ MediaManifest creation and verification
- ✓ CBOR serialization/deserialization (requires cbor2)

## Dependencies

Required:
- `typer` - CLI framework (already used in animica CLI)
- `rich` - Console output (already used in animica CLI)
- `da.media.manifest` - Manifest data structures
- `da.cli.put_blob`, `da.cli.get_blob` - DA utilities

Optional:
- `PIL` (Pillow) - For extracting image dimensions
- `cbor2` - For CBOR serialization (required by MediaManifest)

## Usage Examples

### Upload a single image
```bash
animica ena image put photo.png --name "Beach Sunset" --tag vacation
```

### Upload with multiple tags
```bash
animica ena image put logo.png --name "Company Logo" --tag branding --tag official
```

### Upload large image (auto-chunking)
```bash
# File > 1MB will be automatically chunked
animica ena image put large-image.jpg --name "High Resolution Photo"
```

### List all images
```bash
animica ena image list
```

### Filter by tag
```bash
animica ena image list --tag vacation
```

### Download image
```bash
animica ena image get a1b2c3d4e5f6... --output my-photo.png
```

### Verify image integrity
```bash
animica ena image verify a1b2c3d4e5f6... local-copy.png
```

### JSON output (for scripting)
```bash
animica ena image put photo.png --json > upload-result.json
animica ena image list --json | jq '.images[] | .media_id'
```

## Error Handling

The implementation includes comprehensive error handling:
- Invalid file format validation
- Missing file errors
- DA upload/download failures
- Manifest parsing errors
- Integrity verification failures
- Cache read/write failures

All errors are reported with clear messages and appropriate exit codes.

## Future Enhancements

Potential improvements:
1. Progress bars for large uploads/downloads
2. Parallel chunk upload/download
3. Image thumbnail generation
4. EXIF metadata extraction
5. Support for more image formats (GIF, TIFF, BMP)
6. Deduplication based on content hash
7. Batch upload/download commands
8. Image transformation/resize before upload
9. Encryption support for private images
10. Integration with on-chain registry for discovery

## Changes Made

**Modified file:**
- `python/animica/cli/ena.py` - Added ~860 lines of image CLI implementation

**Key additions:**
1. Created `image_app` typer application
2. Registered as subcommand under `animica ena image`
3. Implemented 4 commands: put, get, verify, list
4. Added image validation with magic byte checking
5. Implemented chunking logic for large files
6. Created local cache management system
7. Integrated with existing DA CLI utilities
8. Added MediaManifest creation and verification

## Verification

To verify the implementation:

```bash
# Check syntax
python3 -m py_compile python/animica/cli/ena.py

# Check commands are registered
grep "@image_app.command" python/animica/cli/ena.py

# Once dependencies are installed, test help
animica ena image --help
animica ena image put --help
animica ena image get --help
animica ena image verify --help
animica ena image list --help
```

## Implementation Complete

All requirements have been met:
- ✅ Use namespace 7 for ENA media
- ✅ Implement chunking for files > 1MB (1MB chunks)
- ✅ Support image formats: png, jpg, jpeg, webp
- ✅ Validate MIME types and magic bytes
- ✅ Compute both SHA-256 and SHA3-256 hashes
- ✅ Create and upload MediaManifest after uploading content
- ✅ Use existing da.cli.put_blob and da.cli.get_blob utilities
- ✅ Store manifests locally in ~/.animica/media_cache.json
- ✅ Support tags via --tag option (multiple)
- ✅ Add --name option for display name
- ✅ Show nice progress output with size, hashes, and manifest commitment
- ✅ Use typer for CLI framework
- ✅ Add as separate image subcommand under ena app

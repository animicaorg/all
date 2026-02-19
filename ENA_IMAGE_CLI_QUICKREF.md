# ENA Image CLI - Quick Reference

## Quick Start

```bash
# Upload an image
animica ena image put photo.png --name "My Photo" --tag vacation

# List all images
animica ena image list

# Download an image
animica ena image get <media_id> --output downloaded.png

# Verify an image
animica ena image verify <media_id> local-file.png
```

## Commands

### `put <file>` - Upload Image

```bash
# Basic upload
animica ena image put image.png

# With name and tags
animica ena image put image.png --name "Sunset" --tag landscape --tag nature

# From specific wallet
animica ena image put image.png --from wallet0

# JSON output
animica ena image put image.png --json
```

**Supported formats:** PNG, JPG, JPEG, WEBP  
**Auto-chunking:** Files > 1MB are automatically chunked into 1MB pieces

### `get <media_id>` - Download Image

```bash
# Download to original filename
animica ena image get a1b2c3d4e5f6789...

# Download to specific file
animica ena image get a1b2c3d4e5f6789... --output my-image.png

# JSON output
animica ena image get a1b2c3d4e5f6789... --json
```

### `verify <media_id> <file>` - Verify Integrity

```bash
# Verify local file matches uploaded version
animica ena image verify a1b2c3d4e5f6789... local-copy.png

# JSON output
animica ena image verify a1b2c3d4e5f6789... local-copy.png --json
```

Checks:
- File size matches
- SHA-256 hash matches
- SHA3-256 hash matches

### `list` - List Uploaded Images

```bash
# List all images
animica ena image list

# Filter by tag
animica ena image list --tag vacation

# JSON output
animica ena image list --json
```

## Options

### Common Options

- `--json` - Output as JSON (all commands)
- `--da-url <url>` - Custom DA service URL (put, get, verify)

### `put` Options

- `--name <name>` - Display name for the image
- `--tag <tag>` - Add tag (can specify multiple times)
- `--from <wallet>` - Wallet identifier (address, label, or index)

### `get` Options

- `--output <path>` or `-o <path>` - Output file path

### `list` Options

- `--tag <tag>` - Filter by tag

## Technical Details

### Image Validation

- **Magic bytes:** Files are validated by checking their magic bytes
- **MIME types:** Automatically assigned based on extension and content
- **Supported extensions:** `.png`, `.jpg`, `.jpeg`, `.webp`

### Chunking

- **Threshold:** Files > 1MB are chunked
- **Chunk size:** 1MB per chunk
- **Automatic:** Chunking and reassembly are automatic and transparent

### Hashing

All images have two hash digests computed:
- **SHA-256:** Standard cryptographic hash
- **SHA3-256:** Keccak-based hash for additional security

### Storage

- **DA namespace:** 7 (ENA media namespace)
- **Content:** Stored in DA layer (chunked if needed)
- **Manifest:** CBOR-encoded metadata stored in DA
- **Local cache:** `~/.animica/media_cache.json`

### Cache Structure

```json
{
  "images": [
    {
      "media_id": "32-byte hex ID",
      "name": "image.png",
      "kind": "image",
      "content_type": "image/png",
      "byte_size": 1234567,
      "tags": ["tag1", "tag2"],
      "created_at": 1234567890,
      "width": 1920,
      "height": 1080,
      "manifest_commitment": "0xabc123..."
    }
  ]
}
```

## Examples

### Upload with metadata
```bash
animica ena image put screenshot.png \
  --name "Dashboard Screenshot" \
  --tag ui \
  --tag reference \
  --tag v2.0
```

### Batch operations (with shell scripting)
```bash
# Upload all PNGs in directory
for img in *.png; do
  animica ena image put "$img" --tag batch
done

# List and save to file
animica ena image list --json > images.json
```

### Download all images with specific tag
```bash
# Get media IDs for tag
MEDIA_IDS=$(animica ena image list --tag vacation --json | \
  jq -r '.images[].media_id')

# Download each
for id in $MEDIA_IDS; do
  animica ena image get "$id"
done
```

### Verify all local files
```bash
# For each cached image, verify local copy if exists
animica ena image list --json | jq -r '.images[] | "\(.media_id) \(.name)"' | \
while read media_id name; do
  if [ -f "$name" ]; then
    echo "Verifying $name..."
    animica ena image verify "$media_id" "$name"
  fi
done
```

## Troubleshooting

### "File not found in cache"

The `get` and `verify` commands require the manifest commitment to be in the local cache. Only images uploaded with this CLI will be in the cache.

**Solution:** The manifest commitment must be known to retrieve the image.

### "Invalid file format"

Ensure the file has the correct extension (`.png`, `.jpg`, `.jpeg`, `.webp`) and valid magic bytes.

**Solution:** Convert the image to a supported format.

### "DA upload failed"

Check the DA service is accessible and you have permissions.

**Solution:** Verify `--da-url` or use default DA service.

### "Verification failed"

The local file doesn't match the uploaded version (corrupted or modified).

**Solution:** Re-download the image using `get` command.

## Integration

### With other animica commands
```bash
# Use wallet from animica CLI
animica ena image put photo.png --from $(animica wallet list --json | jq -r '.wallets[0].address')
```

### Programmatic usage
```python
import subprocess
import json

# Upload image
result = subprocess.run([
    'animica', 'ena', 'image', 'put', 'image.png',
    '--name', 'My Image',
    '--tag', 'example',
    '--json'
], capture_output=True, text=True)

data = json.loads(result.stdout)
media_id = data['media_id']
print(f"Uploaded: {media_id}")
```

## Performance

### Upload speeds
- Small images (< 1MB): ~1-2 seconds
- Large images (> 1MB): ~1-2 seconds per chunk + manifest upload

### Download speeds
- Small images: ~1 second
- Large chunked images: ~1 second per chunk + reassembly

### Verification
- Local hash computation: ~100-500ms depending on file size
- Manifest download: ~1 second

## Limits

- **Max file size:** Limited by DA layer capacity
- **Chunk size:** Fixed at 1MB
- **Supported formats:** PNG, JPG, JPEG, WEBP only
- **Cache size:** Unlimited (local disk space)

## See Also

- Full implementation details: `ENA_IMAGE_CLI_IMPLEMENTATION.md`
- DA layer documentation: `da/README.md`
- MediaManifest spec: `da/media/manifest.py`

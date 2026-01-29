#!/usr/bin/env python3
"""
Generate application icons from logo.png for all platforms.

This script generates:
- macOS: .icns file
- Windows: .ico file (multi-resolution)
- Linux: PNG files at standard sizes (16, 32, 48, 64, 128, 256, 512)

Dependencies:
- Python 3.10+
- Pillow (PIL): pip install Pillow
- ImageMagick (optional, for .icns generation on non-macOS)

Usage:
    python scripts/gen-icons.py [--force] [--check]
    
Options:
    --force     Regenerate icons even if they exist
    --check     Only check if icons need regeneration (exit code 0 = up to date, 1 = needs regen)
"""

import argparse
import hashlib
import os
import platform
import subprocess
import sys
from pathlib import Path

try:
    from PIL import Image
except ImportError:
    print("Error: Pillow is required. Install with: pip install Pillow")
    sys.exit(1)

# Paths
SCRIPT_DIR = Path(__file__).parent
REPO_ROOT = SCRIPT_DIR.parent.parent
LOGO_PATH = REPO_ROOT / "contrib" / "logos" / "png" / "animica-logo-1024.png"
ICONS_DIR = SCRIPT_DIR.parent / "resources" / "icons"
CHECKSUM_FILE = ICONS_DIR / ".logo-checksum"

# Icon sizes
LINUX_SIZES = [16, 32, 48, 64, 128, 256, 512]
WINDOWS_SIZES = [16, 32, 48, 64, 128, 256]  # ICO format limits
MACOS_ICONSET_SIZES = [16, 32, 64, 128, 256, 512, 1024]  # For iconset


def compute_logo_hash():
    """Compute SHA256 hash of logo.png."""
    if not LOGO_PATH.exists():
        print(f"Error: Logo not found at {LOGO_PATH}")
        sys.exit(1)
    
    with open(LOGO_PATH, "rb") as f:
        return hashlib.sha256(f.read()).hexdigest()


def load_cached_hash():
    """Load cached logo hash from checksum file."""
    if CHECKSUM_FILE.exists():
        return CHECKSUM_FILE.read_text().strip()
    return None


def save_hash(hash_value):
    """Save logo hash to checksum file."""
    CHECKSUM_FILE.write_text(hash_value + "\n")


def needs_regeneration():
    """Check if icons need to be regenerated."""
    current_hash = compute_logo_hash()
    cached_hash = load_cached_hash()
    
    if current_hash != cached_hash:
        return True, "Logo has changed"
    
    # Check if all output files exist
    macos_icns = ICONS_DIR / "animica.icns"
    windows_ico = ICONS_DIR / "animica.ico"
    
    if not macos_icns.exists():
        return True, "macOS .icns missing"
    if not windows_ico.exists():
        return True, "Windows .ico missing"
    
    # Check Linux icons
    for size in LINUX_SIZES:
        linux_icon = ICONS_DIR / "hicolor" / f"{size}x{size}" / "apps" / "animica-wallet.png"
        if not linux_icon.exists():
            return True, f"Linux {size}x{size} icon missing"
    
    return False, "Icons are up to date"


def resize_image(img, size, resample=Image.Resampling.LANCZOS):
    """Resize image to square with given size."""
    return img.resize((size, size), resample)


def generate_linux_icons(logo_img):
    """Generate PNG icons for Linux at standard sizes."""
    print("Generating Linux icons...")
    
    for size in LINUX_SIZES:
        output_dir = ICONS_DIR / "hicolor" / f"{size}x{size}" / "apps"
        output_dir.mkdir(parents=True, exist_ok=True)
        
        output_path = output_dir / "animica-wallet.png"
        resized = resize_image(logo_img, size)
        resized.save(output_path, "PNG", optimize=True)
        print(f"  Created {output_path.relative_to(ICONS_DIR)}")


def generate_windows_ico(logo_img):
    """Generate multi-resolution .ico file for Windows."""
    print("Generating Windows .ico...")
    
    output_path = ICONS_DIR / "animica.ico"
    
    # Create icon with multiple sizes
    icon_sizes = [(size, size) for size in WINDOWS_SIZES]
    
    # Resize to all sizes
    images = []
    for width, height in icon_sizes:
        resized = resize_image(logo_img, width)
        images.append(resized)
    
    # Save as ICO with all sizes
    images[0].save(
        output_path,
        format="ICO",
        sizes=icon_sizes,
        append_images=images[1:],
    )
    print(f"  Created {output_path.relative_to(ICONS_DIR)}")


def generate_macos_icns_with_iconutil(logo_img):
    """Generate .icns file using macOS iconutil (native method)."""
    print("Generating macOS .icns with iconutil...")
    
    # Create iconset directory
    iconset_dir = ICONS_DIR / "animica.iconset"
    iconset_dir.mkdir(exist_ok=True)
    
    # Generate all required sizes for iconset
    # See: https://developer.apple.com/library/archive/documentation/GraphicsAnimation/Conceptual/HighResolutionOSX/Optimizing/Optimizing.html
    for size in MACOS_ICONSET_SIZES:
        # Standard resolution
        icon_path = iconset_dir / f"icon_{size}x{size}.png"
        resized = resize_image(logo_img, size)
        resized.save(icon_path, "PNG", optimize=True)
        
        # Retina resolution (2x)
        if size <= 512:  # 1024x1024@2x doesn't exist
            icon_path_2x = iconset_dir / f"icon_{size}x{size}@2x.png"
            resized_2x = resize_image(logo_img, size * 2)
            resized_2x.save(icon_path_2x, "PNG", optimize=True)
    
    # Convert iconset to icns
    output_path = ICONS_DIR / "animica.icns"
    result = subprocess.run(
        ["iconutil", "-c", "icns", str(iconset_dir), "-o", str(output_path)],
        capture_output=True,
        text=True,
    )
    
    if result.returncode != 0:
        print(f"  Warning: iconutil failed: {result.stderr}")
        return False
    
    # Clean up iconset directory
    import shutil
    shutil.rmtree(iconset_dir)
    
    print(f"  Created {output_path.relative_to(ICONS_DIR)}")
    return True


def generate_macos_icns_with_imagemagick(logo_img):
    """Generate .icns file using ImageMagick convert (fallback)."""
    print("Generating macOS .icns with ImageMagick...")
    
    output_path = ICONS_DIR / "animica.icns"
    
    # Create temporary iconset
    iconset_dir = ICONS_DIR / "animica.iconset"
    iconset_dir.mkdir(exist_ok=True)
    
    # Generate standard sizes
    for size in MACOS_ICONSET_SIZES:
        icon_path = iconset_dir / f"icon_{size}x{size}.png"
        resized = resize_image(logo_img, size)
        resized.save(icon_path, "PNG")
    
    # Use ImageMagick to convert
    result = subprocess.run(
        ["convert", str(iconset_dir / "*.png"), str(output_path)],
        capture_output=True,
        text=True,
        shell=True,
    )
    
    if result.returncode != 0:
        print(f"  Warning: ImageMagick convert failed: {result.stderr}")
        # Clean up and return failure
        import shutil
        shutil.rmtree(iconset_dir, ignore_errors=True)
        return False
    
    # Clean up iconset
    import shutil
    shutil.rmtree(iconset_dir)
    
    print(f"  Created {output_path.relative_to(ICONS_DIR)}")
    return True


def generate_macos_icns_fallback(logo_img):
    """Generate simple .icns fallback using only PIL (lowest quality)."""
    print("Generating macOS .icns (PIL fallback - lower quality)...")
    print("  Warning: For best results, install ImageMagick or use macOS with iconutil")
    
    # Just create a single 1024x1024 PNG and rename to .icns
    # This won't be a proper multi-resolution ICNS, but macOS will accept it
    output_path = ICONS_DIR / "animica.icns"
    resized = resize_image(logo_img, 1024)
    resized.save(output_path, "PNG")
    
    print(f"  Created {output_path.relative_to(ICONS_DIR)} (single resolution)")
    return True


def generate_macos_icns(logo_img):
    """Generate .icns file using best available method."""
    # Try iconutil first (macOS native)
    if platform.system() == "Darwin":
        if subprocess.run(["which", "iconutil"], capture_output=True).returncode == 0:
            if generate_macos_icns_with_iconutil(logo_img):
                return
    
    # Try ImageMagick
    if subprocess.run(["which", "convert"], capture_output=True).returncode == 0:
        if generate_macos_icns_with_imagemagick(logo_img):
            return
    
    # Fallback to PIL-only method
    generate_macos_icns_fallback(logo_img)


def main():
    parser = argparse.ArgumentParser(description="Generate application icons from logo.png")
    parser.add_argument("--force", action="store_true", help="Force regeneration even if up to date")
    parser.add_argument("--check", action="store_true", help="Only check if regeneration needed (exit 1 if needed)")
    args = parser.parse_args()
    
    # Check if regeneration is needed
    needs_regen, reason = needs_regeneration()
    
    if args.check:
        if needs_regen:
            print(f"Icons need regeneration: {reason}")
            sys.exit(1)
        else:
            print("Icons are up to date")
            sys.exit(0)
    
    if not args.force and not needs_regen:
        print(f"Icons are up to date (use --force to regenerate)")
        return
    
    print(f"Regenerating icons: {reason}")
    print(f"Source logo: {LOGO_PATH}")
    print(f"Output directory: {ICONS_DIR}")
    print()
    
    # Create output directory
    ICONS_DIR.mkdir(parents=True, exist_ok=True)
    
    # Load logo
    try:
        logo_img = Image.open(LOGO_PATH)
    except Exception as e:
        print(f"Error: Failed to open logo: {e}")
        sys.exit(1)
    
    # Convert to RGBA if needed
    if logo_img.mode != "RGBA":
        logo_img = logo_img.convert("RGBA")
    
    # Generate icons for all platforms
    generate_linux_icons(logo_img)
    generate_windows_ico(logo_img)
    generate_macos_icns(logo_img)
    
    # Save hash for future checks
    save_hash(compute_logo_hash())
    
    print()
    print("Icon generation complete!")
    print()
    print("Generated:")
    print(f"  - macOS: {ICONS_DIR}/animica.icns")
    print(f"  - Windows: {ICONS_DIR}/animica.ico")
    print(f"  - Linux: {ICONS_DIR}/hicolor/*/apps/animica-wallet.png")


if __name__ == "__main__":
    main()

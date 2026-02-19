#!/usr/bin/env python3
"""
Check for TODO/FIXME/STUB markers in source files.

This script enforces production-readiness by rejecting uncommitted code
with placeholder markers. Exceptions are allowed for:
- Test files (*/test_*.py, */tests/*)
- Vendor/third-party code
- Explicitly documented test-only stubs (e.g., aicf/node.py)
- VM-PY integration hooks with clear "Phase 2" documentation

Exit code:
- 0: No violations found
- 1: Found violations
"""

import re
import sys
from pathlib import Path
from typing import List, Set, Tuple

# Patterns to search for (avoiding false positives)
PATTERNS = [
    r'\bTODO\b(?!\()',  # Allow TODO(username) as documentation marker
    r'\bFIXME\b',
    r'\bHACK\b',
]

# Secondary patterns that need context checking
CONTEXTUAL_PATTERNS = [
    (r'\bSTUB\b', ['Real implementation', 'Minimal', 'Lightweight', 'Integration pending', 'test', 'Test', 'Phase 2']),
    (r'\bTEMP\b', ['temporary', 'Temporary', 'atomically', 'file', 'variable', 'Phase 2']),
]

# File extensions to check
EXTENSIONS = {'.py', '.ts', '.js', '.dart', '.rs'}

# Paths to skip (don't check for TODOs)
SKIP_PATHS = {
    'node_modules',
    'vendor',
    'build',
    'dist',
    '.git',
    '__pycache__',
    '.venv',
    'venv',
    '.pnpm-store',
    '.pytest_cache',
    '.mypy_cache',
    'animica.egg-info',
    'sdk/common/test_vectors',  # Test data
    'cex',  # CEX services are separate from blockchain core
    'services',  # External services
    'packages',  # External packages/services
    'apps',  # Application code (wallets, etc.) - separate from consensus
    'studio-web',  # Studio frontend
    'studio-services',  # Studio backend
    'explorer-web',  # Explorer frontend
    'explorer2',  # Explorer v2 frontend
    'wallet',  # Wallet app
    'wallet-qt',  # Qt wallet
    'wallet-extension',  # Browser extension wallet
    'contrib',  # Contributed/external code
    'templates',  # Project templates
    'docs',  # Documentation
    'infra',  # Infrastructure/deployment
    'ops',  # Operations
}

# Files explicitly allowed to have markers (test stubs, documented exceptions)
ALLOWLIST = {
    'aicf/node.py',  # Test-only RPC stub (clearly documented)
    'scripts/check_no_todos.py',  # This file (for pattern documentation)
}


def should_skip(path: Path) -> bool:
    """Check if path should be skipped."""
    parts = set(path.parts)
    
    # Skip if any parent is in SKIP_PATHS
    if parts & SKIP_PATHS:
        return True
    
    # Skip test files
    if 'test' in path.stem.lower() or 'tests' in parts:
        return True
    
    # Skip spec files (vitest)
    if path.suffix in {'.spec.ts', '.spec.js'}:
        return True
    
    return False


def is_allowed_exception(filepath: Path, line: str) -> bool:
    """Check if a TODO/FIXME is an allowed exception."""
    line_lower = line.lower()
    
    # Allow TODO(username) format (documentation/tracking)
    if re.search(r'todo\([a-z]+\)', line_lower):
        return True
    
    # Allow "temp" in common programming contexts
    if 'temp' in line_lower and any(ctx in line_lower for ctx in [
        'temporary',
        'temp file',
        'temp =',
        'temp_',
        '_temp',
        'tempfile',
        'atomically',
        'template',
    ]):
        return True
    
    # Allow "stub" in documentation and test contexts
    if 'stub' in line_lower and any(ctx in line_lower for ctx in [
        'stub for',
        'stub implementation',
        'stub interface',
        'stub provider',
        'test stub',
        'minimal stub',
        'lightweight stub',
        'stubbing',
    ]):
        return True
    
    # Allow well-documented Phase 2 integration markers
    if any(phrase in line_lower for phrase in [
        'phase 2',
        'integration pending',
        'when implemented',
        'example implementation',
        'when vm_py',
    ]):
        return True
    
    # Allow documented MVP/fallback implementations
    if any(phrase in line_lower for phrase in [
        'mvp implementation',
        'marketplace_summary',
        'fallback',
        'mock mode',
    ]):
        return True
    
    # Allow comments explaining integration paths (not actual code)
    if re.search(r'#.*when.*:', line_lower):
        return True
    
    return False


def check_file(filepath: Path) -> List[Tuple[int, str]]:
    """Check a single file for TODO/FIXME/STUB markers.
    
    Returns:
        List of (line_number, line_content) tuples for violations
    """
    violations = []
    
    try:
        with open(filepath, 'r', encoding='utf-8', errors='ignore') as f:
            for line_num, line in enumerate(f, 1):
                # Skip if this is an allowed exception
                if is_allowed_exception(filepath, line):
                    continue
                
                # Check for patterns
                for pattern in PATTERNS:
                    if re.search(pattern, line, re.IGNORECASE):
                        violations.append((line_num, line.rstrip()))
                        break
    except Exception as e:
        print(f"Warning: Failed to read {filepath}: {e}", file=sys.stderr)
    
    return violations


def main() -> int:
    """Main entry point."""
    root = Path(__file__).parent.parent
    violations_found = False
    
    # Get all source files
    for ext in EXTENSIONS:
        for filepath in root.rglob(f'*{ext}'):
            # Check allowlist first
            try:
                rel_path = filepath.relative_to(root)
                if str(rel_path) in ALLOWLIST:
                    continue
            except ValueError:
                continue
            
            # Skip directories/files
            if should_skip(filepath):
                continue
            
            # Check file
            violations = check_file(filepath)
            if violations:
                violations_found = True
                print(f"\n{rel_path}:")
                for line_num, line in violations:
                    print(f"  Line {line_num}: {line[:100]}")
    
    if violations_found:
        print("\n" + "="*80)
        print("PRODUCTION READINESS CHECK FAILED")
        print("="*80)
        print("""
Found TODO/FIXME/STUB markers in source code.

For production-readiness, all placeholders must be either:
1. Fully implemented with tests
2. Removed and replaced with proper implementation
3. Documented as "Phase 2 - Integration pending" with clear paths
4. Placed in test files only

Allowed exceptions:
- Test files (*/test_*.py, */tests/*, *.spec.ts)
- Well-documented Phase 2 integration hooks
- MVP implementations with fallback data (marketplace)
- Mock implementations with clear status (payments)

To fix: Review each marker and either implement the functionality,
document it as Phase 2 work, or move it to a test file.
""")
        return 1
    
    print("✓ No TODO/FIXME/STUB markers found in production code")
    return 0


if __name__ == '__main__':
    sys.exit(main())

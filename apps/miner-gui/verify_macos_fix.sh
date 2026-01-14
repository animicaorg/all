#!/usr/bin/env bash
# Verification script for macOS infinite spawning fix
# Run this to verify the fix is correctly applied

set -euo pipefail

echo "🔍 Verifying macOS Infinite Spawning Fix..."
echo ""

# Check if we're in the right directory
if [[ ! -f "apps/miner-gui/animica_miner_gui/main.py" ]]; then
    echo "❌ Error: Must run from repository root"
    exit 1
fi

# 1. Check Python syntax
echo "1️⃣ Checking Python syntax..."
python3 -m py_compile apps/miner-gui/animica_miner_gui/main.py
echo "   ✅ Syntax valid"
echo ""

# 2. Verify freeze_support is at module level
echo "2️⃣ Verifying freeze_support() placement..."
python3 << 'PYEOF'
import ast
import sys

with open('apps/miner-gui/animica_miner_gui/main.py') as f:
    tree = ast.parse(f.read())

# Check module-level freeze_support
found_at_module_level = False
found_in_guard = False

for node in tree.body:
    # Check module level
    if isinstance(node, ast.Expr) and isinstance(node.value, ast.Call):
        if isinstance(node.value.func, ast.Attribute):
            if (hasattr(node.value.func.value, 'id') and 
                node.value.func.value.id == 'multiprocessing' and
                node.value.func.attr == 'freeze_support'):
                found_at_module_level = True
    
    # Check if it's in guard
    if isinstance(node, ast.If):
        guard_code = ast.unparse(node)
        if 'freeze_support' in guard_code:
            found_in_guard = True

if not found_at_module_level:
    print('   ❌ freeze_support() NOT found at module level!')
    sys.exit(1)

if found_in_guard:
    print('   ❌ freeze_support() should NOT be in if __name__ guard!')
    sys.exit(1)

print('   ✅ freeze_support() correctly placed at module level')
PYEOF
echo ""

# 3. Run unit tests
echo "3️⃣ Running unit tests..."
if command -v pytest &> /dev/null; then
    python3 -m pytest apps/miner-gui/animica_miner_gui/tests/test_main_entry.py -v --tb=short
    echo "   ✅ All tests passed"
else
    echo "   ⚠️  pytest not installed, skipping tests"
    echo "   Install with: pip install pytest"
fi
echo ""

# 4. Check documentation exists
echo "4️⃣ Checking documentation..."
docs=(
    "apps/miner-gui/MACOS_INFINITE_SPAWN_FIX.md"
    "apps/miner-gui/MACOS_FIX_VISUAL_GUIDE.md"
    "apps/miner-gui/PR_SUMMARY_MACOS_INFINITE_SPAWN_FIX.md"
)

for doc in "${docs[@]}"; do
    if [[ -f "$doc" ]]; then
        echo "   ✅ $(basename "$doc")"
    else
        echo "   ❌ Missing: $(basename "$doc")"
    fi
done
echo ""

# 5. Summary
echo "━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━"
echo "✅ Verification Complete!"
echo "━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━"
echo ""
echo "📋 Next Steps:"
echo "   1. Build macOS DMG:"
echo "      cd apps/miner-gui"
echo "      ./build-scripts/build_macos.sh"
echo ""
echo "   2. Test on macOS:"
echo "      - Mount the DMG"
echo "      - Copy .app to Applications"
echo "      - Launch application"
echo "      - Verify single instance opens"
echo ""
echo "   3. If testing is successful:"
echo "      - Merge PR to main"
echo "      - Release updated DMG"
echo ""
echo "📚 Documentation:"
echo "   - Technical: apps/miner-gui/MACOS_INFINITE_SPAWN_FIX.md"
echo "   - Visual:    apps/miner-gui/MACOS_FIX_VISUAL_GUIDE.md"
echo "   - PR Summary: apps/miner-gui/PR_SUMMARY_MACOS_INFINITE_SPAWN_FIX.md"
echo ""

"""Tests for main entry point structure and freeze_support placement."""

import ast
import inspect
import pytest
from pathlib import Path


def test_freeze_support_at_module_level():
    """Test that freeze_support is called at module level, not inside main() or if __name__ guard.
    
    This is critical for PyInstaller on macOS to prevent infinite spawning when the
    .app bundle is opened. The freeze_support() must be at module level (top-level)
    to work correctly with macOS app bundles.
    """
    # Read the main.py file
    main_py_path = Path(__file__).parent.parent / "main.py"
    with open(main_py_path, "r") as f:
        source = f.read()
    
    # Parse the source code
    tree = ast.parse(source)
    
    # Find the main function
    main_func = None
    main_guard = None
    
    for node in ast.walk(tree):
        # Find main function definition
        if isinstance(node, ast.FunctionDef) and node.name == "main":
            main_func = node
        
        # Find if __name__ == "__main__" block
        if isinstance(node, ast.If):
            if isinstance(node.test, ast.Compare):
                if (isinstance(node.test.left, ast.Name) and 
                    node.test.left.id == "__name__"):
                    main_guard = node
    
    assert main_func is not None, "main() function not found"
    assert main_guard is not None, "if __name__ == '__main__' block not found"
    
    # Check that freeze_support is NOT called in main()
    main_func_code = ast.unparse(main_func)
    assert "freeze_support" not in main_func_code, \
        "freeze_support() should NOT be called inside main() function"
    
    # Check that freeze_support is NOT called in the if __name__ guard
    main_guard_code = ast.unparse(main_guard)
    assert "freeze_support" not in main_guard_code, \
        "freeze_support() should NOT be called in if __name__ == '__main__' block (must be at module level)"
    
    # Check that freeze_support IS called at module level (top-level)
    module_level_freeze_support = False
    for node in tree.body:
        if isinstance(node, ast.Expr) and isinstance(node.value, ast.Call):
            if isinstance(node.value.func, ast.Attribute):
                if (isinstance(node.value.func.value, ast.Name) and
                    node.value.func.value.id == "multiprocessing" and
                    node.value.func.attr == "freeze_support"):
                    module_level_freeze_support = True
                    break
    
    assert module_level_freeze_support, \
        "freeze_support() MUST be called at module level (top-level) for PyInstaller macOS compatibility"
    

def test_main_function_signature():
    """Test that main() function has correct signature."""
    try:
        from animica_miner_gui.main import main
    except ImportError as e:
        # Skip if Qt dependencies are not available (headless environment)
        pytest.skip(f"Cannot import main due to missing Qt dependencies: {e}")
    
    # Check return type annotation
    sig = inspect.signature(main)
    assert sig.return_annotation == int, "main() should return int"
    
    # Check no parameters
    assert len(sig.parameters) == 0, "main() should take no parameters"


def test_multiprocessing_import_at_top():
    """Test that multiprocessing is imported at module level."""
    # Read the main.py file
    main_py_path = Path(__file__).parent.parent / "main.py"
    with open(main_py_path, "r") as f:
        source = f.read()
    
    # Parse the source code
    tree = ast.parse(source)
    
    # Check that multiprocessing is imported at module level (top-level)
    top_level_imports = [
        node for node in tree.body 
        if isinstance(node, (ast.Import, ast.ImportFrom))
    ]
    
    multiprocessing_imported = False
    for import_node in top_level_imports:
        if isinstance(import_node, ast.Import):
            for alias in import_node.names:
                if alias.name == "multiprocessing":
                    multiprocessing_imported = True
                    break
    
    assert multiprocessing_imported, \
        "multiprocessing should be imported at module level"


def test_main_has_proper_guard():
    """Test that main code is protected by if __name__ == '__main__' guard."""
    # Read the main.py file
    main_py_path = Path(__file__).parent.parent / "main.py"
    with open(main_py_path, "r") as f:
        lines = f.readlines()
    
    # Find the actual if __name__ == "__main__" line (not in comments)
    guard_line = None
    for i, line in enumerate(lines):
        stripped = line.strip()
        if stripped.startswith('if __name__ == "__main__"') or stripped.startswith("if __name__ == '__main__'"):
            guard_line = i
            break
    
    assert guard_line is not None, "if __name__ == '__main__' guard not found"
    
    # Check that sys.exit(main()) is called after the guard
    remaining_lines = "".join(lines[guard_line:])
    assert "sys.exit(main())" in remaining_lines, \
        "sys.exit(main()) should be called in the guard block"
    
    # Check that freeze_support() is called BEFORE the guard (at module level)
    before_guard_lines = "".join(lines[:guard_line])
    assert "freeze_support()" in before_guard_lines, \
        "freeze_support() should be called at module level, before the if __name__ guard"

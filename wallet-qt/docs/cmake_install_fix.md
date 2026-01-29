# CMake Install Fix for macOS Bundle Target

## Problem
The macOS build was failing with the following error:
```
CMake Error at CMakeLists.txt:273 (install): 
install TARGETS given no BUNDLE DESTINATION for MACOSX_BUNDLE executable target "animica-wallet".
```

## Root Cause
The `animica-wallet` target was declared as a `MACOSX_BUNDLE` (line 179) which creates a `.app` bundle on macOS. However, the install rule (line 273-275) only specified `RUNTIME DESTINATION bin`, which is insufficient for bundle targets. CMake requires an explicit `BUNDLE DESTINATION` for macOS bundles.

## Solution
Modified the install rules to be platform-conditional:

### On macOS (APPLE)
```cmake
install(TARGETS animica-wallet
    BUNDLE DESTINATION "."
)
```
- `BUNDLE DESTINATION "."` installs the `.app` bundle at the install prefix root
- This is the standard macOS convention for application bundles
- RUNTIME DESTINATION is not needed for bundle targets

### On Linux/Windows
```cmake
install(TARGETS animica-wallet
    RUNTIME DESTINATION "${CMAKE_INSTALL_BINDIR}"
)
```
- Regular executable installed to `bin/` directory
- Standard behavior unchanged

## Additional Improvements
1. Added `include(GNUInstallDirs)` to use standard CMake install directory variables
2. Changed hardcoded `bin` to `${CMAKE_INSTALL_BINDIR}` for portability
3. Added explanatory comments in CMakeLists.txt
4. Created `verify_install.sh` script to test installation on both platforms

## Testing
Run the verification script to test the installation:
```bash
cd wallet-qt/scripts
./verify_install.sh
```

The script will:
1. Configure CMake in a temporary directory
2. Build the animica-wallet target
3. Install to a temporary prefix
4. Verify the installation based on platform (bundle on macOS, executable on Linux)

## References
- [CMake install() documentation](https://cmake.org/cmake/help/latest/command/install.html)
- [CMake MACOSX_BUNDLE property](https://cmake.org/cmake/help/latest/prop_tgt/MACOSX_BUNDLE.html)
- [GNUInstallDirs module](https://cmake.org/cmake/help/latest/module/GNUInstallDirs.html)

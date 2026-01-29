cmake_minimum_required(VERSION 3.16)

project(AnimicaWallet VERSION 0.1.0 LANGUAGES CXX)

# Set C++ standard
set(CMAKE_CXX_STANDARD 17)
set(CMAKE_CXX_STANDARD_REQUIRED ON)

# Include custom CMake modules
list(APPEND CMAKE_MODULE_PATH "${CMAKE_CURRENT_SOURCE_DIR}/cmake")

# Find Qt6 packages (Qt5 fallback for older systems)
find_package(Qt6 COMPONENTS Core Widgets Network Sql)
if(NOT Qt6_FOUND)
    message(STATUS "Qt6 not found, trying Qt5...")
    find_package(Qt5 5.15 REQUIRED COMPONENTS Core Widgets Network Sql)
    set(QT_VERSION_MAJOR 5)
else()
    set(QT_VERSION_MAJOR 6)
endif()

# Find OpenSSL (required for wallet crypto)
find_package(OpenSSL REQUIRED)

# Set Qt version for source code
add_compile_definitions(QT_VERSION_MAJOR=${QT_VERSION_MAJOR})

# Enable automoc and autouic
set(CMAKE_AUTOMOC ON)
set(CMAKE_AUTOUIC ON)
set(CMAKE_AUTORCC ON)

# Get git commit hash (best effort)
execute_process(
    COMMAND git rev-parse --short HEAD
    WORKING_DIRECTORY ${CMAKE_SOURCE_DIR}
    OUTPUT_VARIABLE GIT_COMMIT_HASH
    OUTPUT_STRIP_TRAILING_WHITESPACE
    ERROR_QUIET
)
if(NOT GIT_COMMIT_HASH)
    set(GIT_COMMIT_HASH "unknown")
endif()

# Build timestamp
string(TIMESTAMP BUILD_TIMESTAMP "%Y-%m-%d %H:%M:%S UTC" UTC)

# Add version definitions
add_compile_definitions(
    WALLET_VERSION="${PROJECT_VERSION}"
    GIT_COMMIT_HASH="${GIT_COMMIT_HASH}"
    BUILD_TIMESTAMP="${BUILD_TIMESTAMP}"
)

# Source files
set(SOURCES
    src/main.cpp
    src/platform/AppPaths.cpp
    src/rpc/AnimicaRpcClient.cpp
    src/node/NodeManager.cpp
    src/ui/NodeControlWidget.cpp
    src/wallet/WalletAccount.cpp
    src/wallet/EncryptedKeystore.cpp
    src/wallet/AccountManager.cpp
    src/wallet/AddressBook.cpp
    src/wallet/BalanceTracker.cpp
    src/wallet/WalletEngine.cpp
    src/wallet/UnlockDialog.cpp
    src/wallet/AccountsWidget.cpp
    src/wallet/AddressBookWidget.cpp
    src/wallet/CreateAccountDialog.cpp
    src/wallet/WalletWidget.cpp
    src/wallet/WalletDatabase.cpp
    src/wallet/TransactionMonitor.cpp
    src/wallet/FeeEstimator.cpp
    src/wallet/SendWidget.cpp
)

set(HEADERS
    src/platform/AppPaths.h
    src/rpc/AnimicaRpcClient.h
    src/node/NodeManager.h
    src/ui/NodeControlWidget.h
    src/wallet/WalletAccount.h
    src/wallet/EncryptedKeystore.h
    src/wallet/AccountManager.h
    src/wallet/AddressBook.h
    src/wallet/BalanceTracker.h
    src/wallet/WalletEngine.h
    src/wallet/UnlockDialog.h
    src/wallet/AccountsWidget.h
    src/wallet/AddressBookWidget.h
    src/wallet/CreateAccountDialog.h
    src/wallet/WalletWidget.h
    src/wallet/WalletDatabase.h
    src/wallet/TransactionMonitor.h
    src/wallet/FeeEstimator.h
    src/wallet/SendWidget.h
)

# Create executable
add_executable(animica-wallet ${SOURCES} ${HEADERS})

# Compiler-specific warnings
if(MSVC)
    target_compile_options(animica-wallet PRIVATE /W4 /WX-)
else()
    target_compile_options(animica-wallet PRIVATE 
        -Wall -Wextra -Wpedantic
        -Wno-unused-parameter
    )
endif()

# Link Qt libraries
if(QT_VERSION_MAJOR EQUAL 6)
    target_link_libraries(animica-wallet
        Qt6::Core
        Qt6::Widgets
        Qt6::Network
        Qt6::Sql
        OpenSSL::SSL
        OpenSSL::Crypto
    )
else()
    target_link_libraries(animica-wallet
        Qt5::Core
        Qt5::Widgets
        Qt5::Network
        Qt5::Sql
        OpenSSL::SSL
        OpenSSL::Crypto
    )
endif()

# Set output directory
set_target_properties(animica-wallet PROPERTIES
    RUNTIME_OUTPUT_DIRECTORY "${CMAKE_BINARY_DIR}/bin"
)

# Build the Animica node
include(AnimicaNode)
animica_build_node(NODE_PYTHON_PATH)

# Bundle the node into the wallet output
# This creates a platform-specific bundle structure
if(APPLE)
    # macOS: Create app bundle with node inside Resources
    set_target_properties(animica-wallet PROPERTIES
        MACOSX_BUNDLE TRUE
        MACOSX_BUNDLE_GUI_IDENTIFIER "org.animica.wallet"
        MACOSX_BUNDLE_BUNDLE_NAME "AnimicaWallet"
        MACOSX_BUNDLE_BUNDLE_VERSION ${PROJECT_VERSION}
        MACOSX_BUNDLE_SHORT_VERSION_STRING ${PROJECT_VERSION}
        OUTPUT_NAME "AnimicaWallet"
    )
    
    # Copy node venv into app bundle
    set(BUNDLE_NODE_DIR "${CMAKE_BINARY_DIR}/bin/AnimicaWallet.app/Contents/Resources/node")
    add_custom_command(TARGET animica-wallet POST_BUILD
        COMMAND ${CMAKE_COMMAND} -E make_directory "${BUNDLE_NODE_DIR}"
        COMMAND ${CMAKE_COMMAND} -E copy_directory
            "${ANIMICA_NODE_VENV}"
            "${BUNDLE_NODE_DIR}/venv"
        COMMENT "Bundling Animica node into macOS app bundle"
    )
    
    # Create wrapper script for easy access
    set(NODE_WRAPPER "${BUNDLE_NODE_DIR}/animica-node")
    add_custom_command(TARGET animica-wallet POST_BUILD
        COMMAND ${CMAKE_COMMAND} -E echo "#!/bin/bash" > "${NODE_WRAPPER}"
        COMMAND ${CMAKE_COMMAND} -E echo "exec \"\$(dirname \"\$0\")/venv/bin/python\" -m rpc \"\$@\"" >> "${NODE_WRAPPER}"
        COMMAND chmod +x "${NODE_WRAPPER}"
        COMMENT "Creating node wrapper script"
    )
    
elseif(WIN32)
    # Windows: Copy node alongside executable
    set(BUNDLE_NODE_DIR "${CMAKE_BINARY_DIR}/bin/node")
    add_custom_command(TARGET animica-wallet POST_BUILD
        COMMAND ${CMAKE_COMMAND} -E make_directory "${BUNDLE_NODE_DIR}"
        COMMAND ${CMAKE_COMMAND} -E copy_directory
            "${ANIMICA_NODE_VENV}"
            "${BUNDLE_NODE_DIR}/venv"
        COMMENT "Bundling Animica node for Windows"
    )
    
    # Create batch wrapper
    set(NODE_WRAPPER "${BUNDLE_NODE_DIR}/animica-node.bat")
    add_custom_command(TARGET animica-wallet POST_BUILD
        COMMAND ${CMAKE_COMMAND} -E echo "@echo off" > "${NODE_WRAPPER}"
        COMMAND ${CMAKE_COMMAND} -E echo "\"%~dp0venv\\Scripts\\python.exe\" -m rpc %*" >> "${NODE_WRAPPER}"
        COMMENT "Creating node wrapper script"
    )
    
else()
    # Linux: Copy node into dist/node
    set(BUNDLE_NODE_DIR "${CMAKE_BINARY_DIR}/bin/node")
    add_custom_command(TARGET animica-wallet POST_BUILD
        COMMAND ${CMAKE_COMMAND} -E make_directory "${BUNDLE_NODE_DIR}"
        COMMAND ${CMAKE_COMMAND} -E copy_directory
            "${ANIMICA_NODE_VENV}"
            "${BUNDLE_NODE_DIR}/venv"
        COMMENT "Bundling Animica node for Linux"
    )
    
    # Create wrapper script
    set(NODE_WRAPPER "${BUNDLE_NODE_DIR}/animica-node")
    add_custom_command(TARGET animica-wallet POST_BUILD
        COMMAND ${CMAKE_COMMAND} -E echo "#!/bin/bash" > "${NODE_WRAPPER}"
        COMMAND ${CMAKE_COMMAND} -E echo "exec \"\$(dirname \"\$0\")/venv/bin/python\" -m rpc \"\$@\"" >> "${NODE_WRAPPER}"
        COMMAND chmod +x "${NODE_WRAPPER}"
        COMMENT "Creating node wrapper script"
    )
endif()

# Store bundle info for runtime
add_compile_definitions(
    BUNDLED_NODE_PATH="${BUNDLE_NODE_DIR}"
)

# Install target
install(TARGETS animica-wallet
    RUNTIME DESTINATION bin
)

# Print build information
# Add tests subdirectory (optional, enable with -DBUILD_TESTING=ON)
option(BUILD_TESTING "Build tests" ON)
if(BUILD_TESTING)
    enable_testing()
    add_subdirectory(tests)
endif()

message(STATUS "")
message(STATUS "========================================")
message(STATUS "Animica Wallet Configuration")
message(STATUS "========================================")
message(STATUS "Version: ${PROJECT_VERSION}")
message(STATUS "Build type: ${CMAKE_BUILD_TYPE}")
message(STATUS "C++ standard: ${CMAKE_CXX_STANDARD}")
message(STATUS "Qt version: ${Qt6_VERSION}")
message(STATUS "Install prefix: ${CMAKE_INSTALL_PREFIX}")
message(STATUS "Build tests: ${BUILD_TESTING}")
message(STATUS "========================================")
message(STATUS "")

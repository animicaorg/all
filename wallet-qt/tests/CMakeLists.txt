cmake_minimum_required(VERSION 3.16)

# Test configuration
enable_testing()

# Find Qt6 Test module
find_package(Qt6 COMPONENTS Test Widgets Concurrent REQUIRED)
find_package(Python3 REQUIRED COMPONENTS Interpreter)

# Common include directories
set(TEST_INCLUDE_DIRS
    ${CMAKE_SOURCE_DIR}/src
    ${CMAKE_SOURCE_DIR}/src/wallet
    ${CMAKE_SOURCE_DIR}/src/platform
    ${CMAKE_SOURCE_DIR}/src/rpc
    ${CMAKE_SOURCE_DIR}/src/node
    ${CMAKE_SOURCE_DIR}/src/diagnostics
)

get_filename_component(ANIMICA_REPO_ROOT "${CMAKE_SOURCE_DIR}/.." ABSOLUTE)

# Helper function to add Qt tests
function(add_wallet_test TEST_NAME TEST_SOURCE)
    add_executable(${TEST_NAME} ${TEST_SOURCE})
    
    target_link_libraries(${TEST_NAME}
        Qt6::Test
        Qt6::Core
        Qt6::Gui
        Qt6::Widgets
        Qt6::Network
        Qt6::Sql
        Qt6::Concurrent
        OpenSSL::SSL
        OpenSSL::Crypto
    )
    
    target_include_directories(${TEST_NAME} PRIVATE ${TEST_INCLUDE_DIRS})
    
    add_test(NAME ${TEST_NAME} COMMAND ${TEST_NAME})
    
    # Set working directory to build dir
    set_tests_properties(${TEST_NAME} PROPERTIES
        WORKING_DIRECTORY ${CMAKE_BINARY_DIR}
        ENVIRONMENT "QT_QPA_PLATFORM=offscreen"
    )
endfunction()

# Add wallet tests
add_wallet_test(test_keystore_security test_keystore_security.cpp)
add_wallet_test(test_wallet_engine test_wallet_engine.cpp)
add_wallet_test(test_walletdatabase test_walletdatabase.cpp)
add_wallet_test(test_datadirmanager test_datadirmanager.cpp)
add_wallet_test(test_walletimporter test_walletimporter.cpp)
add_wallet_test(test_node_manager test_node_manager.cpp)
add_wallet_test(test_app_paths test_app_paths.cpp)
add_wallet_test(test_receive_qr test_receive_qr.cpp)
add_wallet_test(test_packaging_config test_packaging_config.cpp)

add_test(
    NAME test_linux_layout_resolver
    COMMAND ${Python3_EXECUTABLE} ${CMAKE_SOURCE_DIR}/tests/test_linux_layout_resolver.py
)
set_tests_properties(test_linux_layout_resolver PROPERTIES
    WORKING_DIRECTORY ${CMAKE_BINARY_DIR}
)

add_test(
    NAME test_wallet_manifest_generator
    COMMAND ${Python3_EXECUTABLE} ${CMAKE_SOURCE_DIR}/tests/test_wallet_manifest_generator.py
)
set_tests_properties(test_wallet_manifest_generator PROPERTIES
    WORKING_DIRECTORY ${CMAKE_BINARY_DIR}
)

add_test(
    NAME test_windows_cross_release_scripts
    COMMAND ${Python3_EXECUTABLE} ${CMAKE_SOURCE_DIR}/tests/test_windows_cross_release_scripts.py
)
set_tests_properties(test_windows_cross_release_scripts PROPERTIES
    WORKING_DIRECTORY ${CMAKE_BINARY_DIR}
)

# Add RPC MOC syntax test (verifies AnimicaRpcClient.h compiles with AUTOMOC)
add_wallet_test(test_rpc_moc_syntax test_rpc_moc_syntax.cpp)

# Add diagnostics tests
add_wallet_test(test_redactor diagnostics/test_redactor.cpp)
add_wallet_test(test_allowlist diagnostics/test_allowlist.cpp)
add_wallet_test(test_executor diagnostics/test_executor.cpp)

# Link wallet sources to tests (needed for testing)
target_sources(test_keystore_security PRIVATE
    ${CMAKE_SOURCE_DIR}/src/wallet/EncryptedKeystore.cpp
)

target_sources(test_wallet_engine PRIVATE
    ${CMAKE_SOURCE_DIR}/src/wallet/WalletEngine.cpp
    ${CMAKE_SOURCE_DIR}/src/wallet/AnimicaWalletBackend.cpp
    ${CMAKE_SOURCE_DIR}/src/wallet/WalletAccount.cpp
    ${CMAKE_SOURCE_DIR}/src/wallet/EncryptedKeystore.cpp
    ${CMAKE_SOURCE_DIR}/src/wallet/AccountManager.cpp
    ${CMAKE_SOURCE_DIR}/src/wallet/AddressBook.cpp
    ${CMAKE_SOURCE_DIR}/src/wallet/BalanceTracker.cpp
    ${CMAKE_SOURCE_DIR}/src/platform/AppPaths.cpp
    ${CMAKE_SOURCE_DIR}/src/rpc/AnimicaRpcClient.cpp
    ${CMAKE_SOURCE_DIR}/src/rpc/RpcReply.cpp
)

target_compile_definitions(test_wallet_engine PRIVATE
    ANIMICA_REPO_ROOT_PATH="${ANIMICA_REPO_ROOT}"
)

target_compile_definitions(test_receive_qr PRIVATE
    ANIMICA_REPO_ROOT_PATH="${ANIMICA_REPO_ROOT}"
)

target_compile_definitions(test_packaging_config PRIVATE
    WALLET_QT_SOURCE_DIR="${CMAKE_SOURCE_DIR}"
)

target_sources(test_walletdatabase PRIVATE
    ${CMAKE_SOURCE_DIR}/src/wallet/WalletDatabase.cpp
)

target_sources(test_receive_qr PRIVATE
    ${CMAKE_SOURCE_DIR}/src/wallet/ReceiveWidget.cpp
    ${CMAKE_SOURCE_DIR}/src/wallet/ReceiveQrService.cpp
    ${CMAKE_SOURCE_DIR}/src/wallet/WalletEngine.cpp
    ${CMAKE_SOURCE_DIR}/src/wallet/AnimicaWalletBackend.cpp
    ${CMAKE_SOURCE_DIR}/src/wallet/WalletAccount.cpp
    ${CMAKE_SOURCE_DIR}/src/wallet/EncryptedKeystore.cpp
    ${CMAKE_SOURCE_DIR}/src/wallet/AccountManager.cpp
    ${CMAKE_SOURCE_DIR}/src/wallet/AddressBook.cpp
    ${CMAKE_SOURCE_DIR}/src/wallet/BalanceTracker.cpp
    ${CMAKE_SOURCE_DIR}/src/platform/AppPaths.cpp
    ${CMAKE_SOURCE_DIR}/src/rpc/AnimicaRpcClient.cpp
    ${CMAKE_SOURCE_DIR}/src/rpc/RpcReply.cpp
)

# Link diagnostics sources to tests
target_sources(test_redactor PRIVATE
    ${CMAKE_SOURCE_DIR}/src/diagnostics/Redactor.cpp
)

target_sources(test_allowlist PRIVATE
    ${CMAKE_SOURCE_DIR}/src/diagnostics/CommandAllowlist.cpp
    ${CMAKE_SOURCE_DIR}/src/diagnostics/RoleManager.cpp
)

target_sources(test_executor PRIVATE
    ${CMAKE_SOURCE_DIR}/src/diagnostics/ConsoleExecutor.cpp
    ${CMAKE_SOURCE_DIR}/src/diagnostics/Redactor.cpp
    ${CMAKE_SOURCE_DIR}/src/rpc/AnimicaRpcClient.cpp
    ${CMAKE_SOURCE_DIR}/src/rpc/RpcReply.cpp
    ${CMAKE_SOURCE_DIR}/src/platform/AppPaths.cpp
)

# Test discovery for CTest
include(CTest)

message(STATUS "Wallet tests configured - use 'make test' or 'ctest' to run")

# Link new test sources
target_sources(test_datadirmanager PRIVATE
    ${CMAKE_SOURCE_DIR}/src/platform/DataDirManager.cpp
)

target_sources(test_walletimporter PRIVATE
    ${CMAKE_SOURCE_DIR}/src/wallet/WalletImporter.cpp
)

# Link RPC client source to MOC syntax test
target_sources(test_rpc_moc_syntax PRIVATE
    ${CMAKE_SOURCE_DIR}/src/rpc/AnimicaRpcClient.cpp
    ${CMAKE_SOURCE_DIR}/src/rpc/RpcReply.cpp
)

# Link node manager sources to test
target_sources(test_node_manager PRIVATE
    ${CMAKE_SOURCE_DIR}/src/node/NodeManager.cpp
    ${CMAKE_SOURCE_DIR}/src/platform/AppPaths.cpp
    ${CMAKE_SOURCE_DIR}/src/platform/DataDirManager.cpp
    ${CMAKE_SOURCE_DIR}/src/rpc/AnimicaRpcClient.cpp
    ${CMAKE_SOURCE_DIR}/src/rpc/RpcReply.cpp
)

target_sources(test_app_paths PRIVATE
    ${CMAKE_SOURCE_DIR}/src/platform/AppPaths.cpp
)

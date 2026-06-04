cmake_minimum_required(VERSION 3.16)

project(AnimicaWallet VERSION 0.2.0 LANGUAGES CXX)

set(WALLET_DISPLAY_NAME "Animica Wallet")
set(WALLET_BUNDLE_NAME "AnimicaWallet")
set(WALLET_GUI_IDENTIFIER "org.animica.wallet")
set(WALLET_COPYRIGHT "Copyright (c) 2026 Animica")
set(WALLET_CANONICAL_RPC_URL "https://rpc.animica.org/rpc")
set(WALLET_CANONICAL_NETWORK "mainnet")

configure_file(
    "${CMAKE_CURRENT_SOURCE_DIR}/resources/macos/Info.plist.in"
    "${CMAKE_CURRENT_BINARY_DIR}/Info.plist"
    @ONLY
)

option(WALLET_ENABLE_QT_INSTALL_DEPLOYMENT "Use Qt's install-time deployment script when supported" ON)
option(WALLET_BUNDLE_PYTHON_RUNTIME "Bundle a dedicated Python runtime for wallet backend and QR services" ON)
option(BUILD_TESTING "Build tests" ON)

set(CMAKE_CXX_STANDARD 17)
set(CMAKE_CXX_STANDARD_REQUIRED ON)

include(GNUInstallDirs)

if(WIN32)
    set(CMAKE_INSTALL_BINDIR ".")
endif()

get_filename_component(ANIMICA_REPO_ROOT "${CMAKE_CURRENT_SOURCE_DIR}/.." ABSOLUTE)

if(WALLET_BUNDLE_PYTHON_RUNTIME)
    include("${CMAKE_CURRENT_SOURCE_DIR}/cmake/AnimicaNode.cmake")
    animica_build_node(ANIMICA_NODE_PYTHON)
endif()

find_package(Qt6 COMPONENTS Core Widgets Network Sql Concurrent)
if(NOT Qt6_FOUND)
    message(STATUS "Qt6 not found, trying Qt5...")
    find_package(Qt5 5.15 REQUIRED COMPONENTS Core Widgets Network Sql Concurrent)
    set(QT_VERSION_MAJOR 5)
else()
    set(QT_VERSION_MAJOR 6)
endif()

find_package(OpenSSL REQUIRED)

add_compile_definitions(
    QT_VERSION_MAJOR=${QT_VERSION_MAJOR}
    WALLET_VERSION="${PROJECT_VERSION}"
    WALLET_CANONICAL_RPC_URL="${WALLET_CANONICAL_RPC_URL}"
    WALLET_CANONICAL_NETWORK="${WALLET_CANONICAL_NETWORK}"
)

set(CMAKE_AUTOMOC ON)
set(CMAKE_AUTOUIC ON)
set(CMAKE_AUTORCC ON)

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

string(TIMESTAMP BUILD_TIMESTAMP "%Y-%m-%d %H:%M:%S UTC" UTC)

add_compile_definitions(
    GIT_COMMIT_HASH="${GIT_COMMIT_HASH}"
    BUILD_TIMESTAMP="${BUILD_TIMESTAMP}"
)

set(SOURCES
    src/main.cpp
    resources/wallet-qt.qrc
    src/platform/AppPaths.cpp
    src/platform/DataDirManager.cpp
    src/rpc/AnimicaRpcClient.cpp
    src/rpc/RpcReply.cpp
    src/rpc/RpcSettings.cpp
    src/wallet/WalletAccount.cpp
    src/wallet/AnimicaWalletBackend.cpp
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
    src/wallet/TransactionHistoryWidget.cpp
    src/wallet/FeeEstimator.cpp
    src/wallet/SendWidget.cpp
    src/wallet/ReconciliationJob.cpp
    src/wallet/ReceiveWidget.cpp
    src/wallet/ReceiveQrService.cpp
    src/wallet/ContractInteractionWidget.cpp
    src/wallet/SettingsWidget.cpp
    src/wallet/WalletImporter.cpp
    src/wallet/WalletExporter.cpp
)

set(HEADERS
    src/platform/AppPaths.h
    src/platform/DataDirManager.h
    src/rpc/AnimicaRpcClient.h
    src/rpc/RpcReply.h
    src/rpc/RpcSettings.h
    src/wallet/WalletAccount.h
    src/wallet/AnimicaWalletBackend.h
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
    src/wallet/TransactionHistoryWidget.h
    src/wallet/FeeEstimator.h
    src/wallet/SendWidget.h
    src/wallet/WalletSecuritySettings.h
    src/wallet/ReconciliationJob.h
    src/wallet/ReceiveWidget.h
    src/wallet/ReceiveQrService.h
    src/wallet/ContractInteractionWidget.h
    src/wallet/SettingsWidget.h
    src/wallet/WalletImporter.h
    src/wallet/WalletExporter.h
)

if(APPLE)
    add_executable(animica-wallet MACOSX_BUNDLE ${SOURCES} ${HEADERS})
elseif(WIN32)
    add_executable(animica-wallet WIN32 ${SOURCES} ${HEADERS})
else()
    add_executable(animica-wallet ${SOURCES} ${HEADERS})
endif()

if(WALLET_ENABLE_QT_INSTALL_DEPLOYMENT AND QT_VERSION_MAJOR EQUAL 6 AND (WIN32 OR APPLE))
    qt_generate_deploy_app_script(
        TARGET animica-wallet
        OUTPUT_SCRIPT qt_deploy_script
        NO_UNSUPPORTED_PLATFORM_ERROR
    )
endif()

if(WIN32)
    file(GENERATE
        OUTPUT "${CMAKE_CURRENT_BINARY_DIR}/qt.conf"
        CONTENT "[Paths]\nPrefix = .\nPlugins = plugins\nTranslations = translations\n"
    )
endif()

if(MSVC)
    target_compile_options(animica-wallet PRIVATE /W4 /WX-)
else()
    target_compile_options(animica-wallet PRIVATE
        -Wall -Wextra -Wpedantic
        -Wno-unused-parameter
    )
endif()

if(QT_VERSION_MAJOR EQUAL 6)
    target_link_libraries(animica-wallet
        Qt6::Core
        Qt6::Widgets
        Qt6::Network
        Qt6::Sql
        Qt6::Concurrent
        OpenSSL::SSL
        OpenSSL::Crypto
    )
else()
    target_link_libraries(animica-wallet
        Qt5::Core
        Qt5::Widgets
        Qt5::Network
        Qt5::Sql
        Qt5::Concurrent
        OpenSSL::SSL
        OpenSSL::Crypto
    )
endif()

set_target_properties(animica-wallet PROPERTIES
    RUNTIME_OUTPUT_DIRECTORY "${CMAKE_BINARY_DIR}/bin"
)

if(APPLE)
    # Drop load commands for dylibs whose symbols aren't used. Combined with the
    # POST_BUILD dedup below, this prevents the "duplicate linked dylib" dyld
    # abort on macOS 13+ (OpenGL.framework was being linked twice by the
    # cross-compile toolchain). See scripts/fix_macho_duplicate_dylibs.py.
    target_link_options(animica-wallet PRIVATE "-Wl,-dead_strip_dylibs")
endif()

if(APPLE)
    set_target_properties(animica-wallet PROPERTIES
        MACOSX_BUNDLE TRUE
        MACOSX_BUNDLE_GUI_IDENTIFIER "${WALLET_GUI_IDENTIFIER}"
        MACOSX_BUNDLE_BUNDLE_NAME "${WALLET_BUNDLE_NAME}"
        MACOSX_BUNDLE_BUNDLE_VERSION ${PROJECT_VERSION}
        MACOSX_BUNDLE_SHORT_VERSION_STRING ${PROJECT_VERSION}
        MACOSX_BUNDLE_ICON_FILE "animica.icns"
        MACOSX_BUNDLE_INFO_PLIST "${CMAKE_CURRENT_BINARY_DIR}/Info.plist"
        OUTPUT_NAME "${WALLET_BUNDLE_NAME}"
    )

    set(MACOS_ICON "${CMAKE_CURRENT_SOURCE_DIR}/resources/icons/animica.icns")
    if(EXISTS "${MACOS_ICON}")
        set_source_files_properties("${MACOS_ICON}" PROPERTIES
            MACOSX_PACKAGE_LOCATION "Resources")
        target_sources(animica-wallet PRIVATE "${MACOS_ICON}")
    endif()

    # Belt-and-suspenders: strip any remaining duplicate LC_LOAD_DYLIB load
    # commands from the built binary (dyld on macOS 13+ aborts otherwise).
    # No-op if python3/lief is unavailable, so it never breaks the build.
    find_program(PYTHON3_EXECUTABLE NAMES python3 python)
    if(PYTHON3_EXECUTABLE)
        add_custom_command(TARGET animica-wallet POST_BUILD
            COMMAND "${PYTHON3_EXECUTABLE}"
                    "${CMAKE_CURRENT_SOURCE_DIR}/scripts/fix_macho_duplicate_dylibs.py"
                    "$<TARGET_FILE:animica-wallet>"
            COMMENT "Removing duplicate Mach-O load commands (dyld safety)"
            VERBATIM)
    endif()
elseif(WIN32)
    set(WINDOWS_ICON "${CMAKE_CURRENT_SOURCE_DIR}/resources/icons/animica.ico")
    if(EXISTS "${WINDOWS_ICON}")
        set(RC_FILE "${CMAKE_BINARY_DIR}/animica-wallet.rc")
        configure_file(
            "${CMAKE_CURRENT_SOURCE_DIR}/resources/windows/animica-wallet.rc.in"
            "${RC_FILE}"
            @ONLY
        )
        target_sources(animica-wallet PRIVATE "${RC_FILE}")
    endif()
endif()

if(APPLE)
    install(TARGETS animica-wallet
        BUNDLE DESTINATION "."
    )
else()
    install(TARGETS animica-wallet
        RUNTIME DESTINATION "${CMAKE_INSTALL_BINDIR}"
    )
endif()

if(WALLET_BUNDLE_PYTHON_RUNTIME)
    get_filename_component(ANIMICA_NODE_BIN_DIR "${ANIMICA_NODE_PYTHON}" DIRECTORY)
    get_filename_component(ANIMICA_NODE_VENV_DIR "${ANIMICA_NODE_BIN_DIR}" DIRECTORY)

    if(APPLE)
        set(WALLET_NODE_INSTALL_DESTINATION "${WALLET_BUNDLE_NAME}.app/Contents/Resources/node")
    elseif(WIN32)
        set(WALLET_NODE_INSTALL_DESTINATION "node")
    else()
        set(WALLET_NODE_INSTALL_DESTINATION "${CMAKE_INSTALL_LIBDIR}/animica-wallet/node")
    endif()

    install(
        DIRECTORY "${ANIMICA_NODE_VENV_DIR}/"
        DESTINATION "${WALLET_NODE_INSTALL_DESTINATION}/venv"
        USE_SOURCE_PERMISSIONS
    )

    # Rewrite shebangs in the bundled venv's bin/ to be portable.
    # The build-time venv has shebangs like
    #   #!/root/animica/wallet-qt/build/linux-release/animica-node/venv/bin/python
    # which obviously don't exist on the user's machine. Replace them with
    # `#!/usr/bin/env python3` and let the venv's pyvenv.cfg+bundled
    # site-packages take it from there. Only applies to UNIX-ish targets;
    # Windows .exe wrappers aren't shebangs and never had this problem.
    if(UNIX)
        install(CODE "
            file(GLOB VENV_BIN_FILES \"\$ENV{DESTDIR}\${CMAKE_INSTALL_PREFIX}/${WALLET_NODE_INSTALL_DESTINATION}/venv/bin/*\")
            foreach(BIN_FILE \${VENV_BIN_FILES})
                if(NOT IS_SYMLINK \"\${BIN_FILE}\" AND NOT IS_DIRECTORY \"\${BIN_FILE}\")
                    file(READ \"\${BIN_FILE}\" CONTENT LIMIT 4096 OFFSET 0 HEX)
                    string(SUBSTRING \"\${CONTENT}\" 0 4 MAGIC)
                    # 23 21 = '#!' — only patch text scripts, skip ELF / Mach-O / unrelated.
                    if(\"\${MAGIC}\" MATCHES \"^2321\")
                        file(READ \"\${BIN_FILE}\" TEXT)
                        string(REGEX REPLACE
                            \"^#![^\\n]*\"
                            \"#!/usr/bin/env python3\"
                            PATCHED
                            \"\${TEXT}\")
                        if(NOT \"\${PATCHED}\" STREQUAL \"\${TEXT}\")
                            file(WRITE \"\${BIN_FILE}\" \"\${PATCHED}\")
                            message(STATUS \"shebang-fix: \${BIN_FILE}\")
                        endif()
                    endif()
                endif()
            endforeach()
        ")
    endif()

    install(
        FILES "${ANIMICA_REPO_ROOT}/spec/params.yaml"
        DESTINATION "${WALLET_NODE_INSTALL_DESTINATION}/assets/spec"
    )

    foreach(WALLET_GENESIS_FILE IN ITEMS mainnet.json testnet.json devnet.json)
        set(WALLET_GENESIS_SOURCE "")
        if(EXISTS "${ANIMICA_REPO_ROOT}/core/genesis/${WALLET_GENESIS_FILE}")
            set(WALLET_GENESIS_SOURCE "${ANIMICA_REPO_ROOT}/core/genesis/${WALLET_GENESIS_FILE}")
        elseif(WALLET_GENESIS_FILE STREQUAL "mainnet.json" AND EXISTS "${ANIMICA_REPO_ROOT}/genesis/genesis.sample.mainnet.json")
            set(WALLET_GENESIS_SOURCE "${ANIMICA_REPO_ROOT}/genesis/genesis.sample.mainnet.json")
        elseif(WALLET_GENESIS_FILE STREQUAL "testnet.json" AND EXISTS "${ANIMICA_REPO_ROOT}/genesis/genesis.sample.testnet.json")
            set(WALLET_GENESIS_SOURCE "${ANIMICA_REPO_ROOT}/genesis/genesis.sample.testnet.json")
        elseif(WALLET_GENESIS_FILE STREQUAL "devnet.json" AND EXISTS "${ANIMICA_REPO_ROOT}/genesis/genesis.sample.devnet.json")
            set(WALLET_GENESIS_SOURCE "${ANIMICA_REPO_ROOT}/genesis/genesis.sample.devnet.json")
        endif()

        if(WALLET_GENESIS_SOURCE STREQUAL "")
            message(FATAL_ERROR "No genesis asset found for ${WALLET_GENESIS_FILE} when bundling wallet runtime.")
        endif()

        install(
            FILES "${WALLET_GENESIS_SOURCE}"
            DESTINATION "${WALLET_NODE_INSTALL_DESTINATION}/assets/genesis"
            RENAME "${WALLET_GENESIS_FILE}"
        )
    endforeach()

    if(EXISTS "${ANIMICA_REPO_ROOT}/core/genesis/genesis.json")
        install(
            FILES "${ANIMICA_REPO_ROOT}/core/genesis/genesis.json"
            DESTINATION "${WALLET_NODE_INSTALL_DESTINATION}/assets/genesis"
            RENAME "genesis.json"
        )
    endif()
endif()

if(UNIX AND NOT APPLE)
    install(FILES "${CMAKE_CURRENT_SOURCE_DIR}/resources/linux/animica-wallet.desktop"
        DESTINATION "${CMAKE_INSTALL_DATADIR}/applications"
    )
    foreach(ICON_SIZE 16 32 48 64 128 256 512)
        install(FILES "${CMAKE_CURRENT_SOURCE_DIR}/resources/icons/hicolor/${ICON_SIZE}x${ICON_SIZE}/apps/animica-wallet.png"
            DESTINATION "${CMAKE_INSTALL_DATADIR}/icons/hicolor/${ICON_SIZE}x${ICON_SIZE}/apps"
        )
    endforeach()
endif()

if(WALLET_ENABLE_QT_INSTALL_DEPLOYMENT AND QT_VERSION_MAJOR EQUAL 6 AND (WIN32 OR APPLE) AND qt_deploy_script)
    install(SCRIPT "${qt_deploy_script}")
endif()

if(WIN32)
    install(FILES "${CMAKE_CURRENT_BINARY_DIR}/qt.conf"
        DESTINATION "."
    )
endif()

if(BUILD_TESTING)
    enable_testing()
    add_subdirectory(tests)
endif()

set(CPACK_PACKAGE_NAME "AnimicaWallet")
set(CPACK_PACKAGE_VENDOR "Animica")
set(CPACK_PACKAGE_CONTACT "support@animica.org")
set(CPACK_PACKAGE_DESCRIPTION_SUMMARY "Animica remote desktop wallet for hosted RPC access")
set(CPACK_PACKAGE_INSTALL_DIRECTORY "${WALLET_DISPLAY_NAME}")
set(CPACK_PACKAGE_EXECUTABLES "animica-wallet" "${WALLET_DISPLAY_NAME}")
set(CPACK_CREATE_DESKTOP_LINKS "animica-wallet")
set(CPACK_PACKAGE_VERSION "${PROJECT_VERSION}")
set(CPACK_PACKAGE_FILE_NAME "AnimicaWallet-${PROJECT_VERSION}-${CMAKE_SYSTEM_NAME}-${CMAKE_SYSTEM_PROCESSOR}")
set(CPACK_RESOURCE_FILE_README "${CMAKE_CURRENT_SOURCE_DIR}/README.md")

if(WIN32)
    set(CPACK_WIX_UPGRADE_GUID "B17B9AE5-6D27-4D7F-95E7-9D1C98E76E2F")
    set(CPACK_WIX_PRODUCT_ICON "${CMAKE_CURRENT_SOURCE_DIR}/resources/icons/animica.ico")
    set(CPACK_WIX_PROGRAM_MENU_FOLDER "${WALLET_DISPLAY_NAME}")
    set(CPACK_WIX_UI_REF "WixUI_InstallDir")
endif()

include(CPack)

message(STATUS "")
message(STATUS "========================================")
message(STATUS "Animica Wallet Configuration")
message(STATUS "========================================")
message(STATUS "Version: ${PROJECT_VERSION}")
message(STATUS "Build type: ${CMAKE_BUILD_TYPE}")
message(STATUS "Canonical RPC: ${WALLET_CANONICAL_RPC_URL}")
message(STATUS "Network: ${WALLET_CANONICAL_NETWORK}")
message(STATUS "Qt install deployment: ${WALLET_ENABLE_QT_INSTALL_DEPLOYMENT}")
message(STATUS "Install prefix: ${CMAKE_INSTALL_PREFIX}")
message(STATUS "Build tests: ${BUILD_TESTING}")
message(STATUS "========================================")
message(STATUS "")

cmake_minimum_required(VERSION 3.16)

project(AnimicaWallet VERSION 0.1.0 LANGUAGES CXX)

# Set C++ standard
set(CMAKE_CXX_STANDARD 17)
set(CMAKE_CXX_STANDARD_REQUIRED ON)

# Find Qt6 packages
find_package(Qt6 REQUIRED COMPONENTS Core Widgets Network)

# Enable automoc and autouic
set(CMAKE_AUTOMOC ON)
set(CMAKE_AUTOUIC ON)
set(CMAKE_AUTORCC ON)

# Source files
set(SOURCES
    src/main.cpp
    src/platform/AppPaths.cpp
    src/rpc/AnimicaRpcClient.cpp
    src/node/NodeManager.cpp
    src/ui/NodeControlWidget.cpp
)

set(HEADERS
    src/platform/AppPaths.h
    src/rpc/AnimicaRpcClient.h
    src/node/NodeManager.h
    src/ui/NodeControlWidget.h
)

# Create executable
add_executable(animica-wallet ${SOURCES} ${HEADERS})

# Link Qt libraries
target_link_libraries(animica-wallet
    Qt6::Core
    Qt6::Widgets
    Qt6::Network
)

# Set output directory
set_target_properties(animica-wallet PROPERTIES
    RUNTIME_OUTPUT_DIRECTORY "${CMAKE_BINARY_DIR}/bin"
)

# Install target
install(TARGETS animica-wallet
    RUNTIME DESTINATION bin
)

# Print build information
message(STATUS "")
message(STATUS "========================================")
message(STATUS "Animica Wallet Configuration")
message(STATUS "========================================")
message(STATUS "Version: ${PROJECT_VERSION}")
message(STATUS "Build type: ${CMAKE_BUILD_TYPE}")
message(STATUS "C++ standard: ${CMAKE_CXX_STANDARD}")
message(STATUS "Qt version: ${Qt6_VERSION}")
message(STATUS "Install prefix: ${CMAKE_INSTALL_PREFIX}")
message(STATUS "========================================")
message(STATUS "")

cmake_minimum_required(VERSION 3.21)

project(AnimicaNodeKit LANGUAGES CXX)

set(CMAKE_CXX_STANDARD 17)
set(CMAKE_CXX_STANDARD_REQUIRED ON)

find_package(Qt6 REQUIRED COMPONENTS Core Network)
find_package(OpenSSL REQUIRED)

add_library(AnimicaNodeKit
    src/HealthMonitor.cpp
    src/Keystore.cpp
    src/NodeKit.cpp
    src/ProcessManager.cpp
    src/RpcClient.cpp
    src/SnapshotManager.cpp
)

target_include_directories(AnimicaNodeKit PUBLIC
    ${CMAKE_CURRENT_SOURCE_DIR}/include
)

target_link_libraries(AnimicaNodeKit
    PUBLIC Qt6::Core Qt6::Network OpenSSL::Crypto
)

add_subdirectory(tests)

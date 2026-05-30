cmake_minimum_required(VERSION 3.16)

set(CMAKE_SYSTEM_NAME Windows)
set(CMAKE_SYSTEM_PROCESSOR x86_64)
set(CMAKE_CROSSCOMPILING ON)

set(MINGW_COMPILER_PREFIX "x86_64-w64-mingw32" CACHE STRING "MinGW-w64 compiler prefix")
set(QT_HOST_PATH "" CACHE PATH "Qt host tools prefix used for moc/rcc/uic during cross compilation")
set(WINDOWS_EXTRA_PREFIX_PATHS "" CACHE STRING "Additional semicolon-separated prefix paths for Windows dependencies")

find_program(CMAKE_C_COMPILER NAMES "${MINGW_COMPILER_PREFIX}-gcc" REQUIRED)
find_program(CMAKE_CXX_COMPILER NAMES "${MINGW_COMPILER_PREFIX}-g++" REQUIRED)
find_program(CMAKE_RC_COMPILER NAMES "${MINGW_COMPILER_PREFIX}-windres" REQUIRED)

set(CMAKE_FIND_ROOT_PATH_MODE_PROGRAM NEVER)
set(CMAKE_FIND_ROOT_PATH_MODE_LIBRARY BOTH)
set(CMAKE_FIND_ROOT_PATH_MODE_INCLUDE BOTH)
set(CMAKE_FIND_ROOT_PATH_MODE_PACKAGE BOTH)

set(_wallet_prefix_path "${CMAKE_PREFIX_PATH}")
if(WINDOWS_EXTRA_PREFIX_PATHS)
    list(APPEND _wallet_prefix_path ${WINDOWS_EXTRA_PREFIX_PATHS})
endif()
list(REMOVE_DUPLICATES _wallet_prefix_path)
set(CMAKE_PREFIX_PATH "${_wallet_prefix_path}" CACHE STRING "Cross-compilation dependency prefixes" FORCE)
set(CMAKE_FIND_ROOT_PATH "${_wallet_prefix_path}" CACHE STRING "Root paths searched for cross-compilation deps" FORCE)

if(QT_HOST_PATH)
    set(QT_HOST_PATH "${QT_HOST_PATH}" CACHE PATH "Qt host tools prefix" FORCE)
endif()

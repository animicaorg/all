cmake_minimum_required(VERSION 3.21)

project(AnimicaQtNodeFirst LANGUAGES CXX)

set(CMAKE_CXX_STANDARD 17)
set(CMAKE_CXX_STANDARD_REQUIRED ON)

option(BUILD_QT_APPS "Build Qt applications" ON)

add_subdirectory(nodekit)

if (BUILD_QT_APPS)
    add_subdirectory(qt)
endif()

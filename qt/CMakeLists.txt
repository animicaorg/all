cmake_minimum_required(VERSION 3.21)

project(AnimicaQtApps LANGUAGES CXX)

set(CMAKE_CXX_STANDARD 17)
set(CMAKE_CXX_STANDARD_REQUIRED ON)

find_package(Qt6 REQUIRED COMPONENTS Core Quick Qml QuickControls2 Network)

add_subdirectory(apps/wallet)
add_subdirectory(apps/nodepanel)

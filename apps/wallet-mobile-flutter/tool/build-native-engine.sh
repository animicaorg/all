#!/usr/bin/env bash
# Build the Serve & Earn native engine: llama.cpp's llama-server for
# arm64-v8a Android with the Vulkan backend, stripped and dropped into the
# `serve` flavor's jniLibs as libanmllamaserver.so (the .so name is what lets
# Gradle package it; the app execs it from nativeLibraryDir — see
# lib/services/native_engine.dart and MainActivity.kt).
#
# The recipe below exists because a plain cross-configure fails three times
# over: the NDK ships vulkan.h but not the C++ vulkan.hpp bindings ggml-vulkan
# needs, ships glslc but no SPIRV-Headers cmake package, and the Android
# toolchain's find-root mode ignores host CMAKE_PREFIX_PATH. Khronos header
# repos + explicit *_DIR/include flags fix all three.
#
#   bash tool/build-native-engine.sh            # builds from ../../.. siblings
#   LLAMA_REF=b3c3b96 bash tool/build-native-engine.sh
set -euo pipefail

NDK="${ANDROID_NDK:-/opt/android-sdk/ndk/28.2.13676358}"
WORK="${NATIVE_ENGINE_WORK:-/root}"
LLAMA_REF="${LLAMA_REF:-}"          # empty = whatever `git clone --depth 1` gives
APP_DIR="$(cd "$(dirname "$0")/.." && pwd)"
OUT="$APP_DIR/android/app/src/serve/jniLibs/arm64-v8a/libanmllamaserver.so"

[ -d "$WORK/llama.cpp" ] || git clone --depth 1 https://github.com/ggml-org/llama.cpp.git "$WORK/llama.cpp"
[ -d "$WORK/vulkan-headers" ] || git clone --depth 1 https://github.com/KhronosGroup/Vulkan-Headers "$WORK/vulkan-headers"
if [ ! -f "$WORK/spirv-install/share/cmake/SPIRV-Headers/SPIRV-HeadersConfig.cmake" ]; then
  [ -d "$WORK/spirv-headers" ] || git clone --depth 1 https://github.com/KhronosGroup/SPIRV-Headers "$WORK/spirv-headers"
  cmake -B "$WORK/spirv-headers/build" -S "$WORK/spirv-headers" -DCMAKE_INSTALL_PREFIX="$WORK/spirv-install" >/dev/null
  cmake --build "$WORK/spirv-headers/build" --target install >/dev/null
fi
if [ -n "$LLAMA_REF" ]; then
  git -C "$WORK/llama.cpp" fetch --depth 1 origin "$LLAMA_REF" && git -C "$WORK/llama.cpp" checkout FETCH_HEAD
fi

export PATH="$NDK/shader-tools/linux-x86_64:$PATH"
SYSROOT="$NDK/toolchains/llvm/prebuilt/linux-x86_64/sysroot"
cd "$WORK/llama.cpp"
rm -rf build-android
cmake -B build-android \
  -DCMAKE_TOOLCHAIN_FILE="$NDK/build/cmake/android.toolchain.cmake" \
  -DANDROID_ABI=arm64-v8a -DANDROID_PLATFORM=android-28 \
  -DGGML_VULKAN=ON -DGGML_OPENMP=OFF -DBUILD_SHARED_LIBS=OFF \
  -DLLAMA_CURL=OFF -DLLAMA_BUILD_TESTS=OFF -DLLAMA_BUILD_EXAMPLES=OFF -DLLAMA_BUILD_SERVER=ON \
  -DCMAKE_BUILD_TYPE=Release \
  "-DSPIRV-Headers_DIR=$WORK/spirv-install/share/cmake/SPIRV-Headers" \
  -DCMAKE_FIND_ROOT_PATH_MODE_PACKAGE=BOTH \
  -DVulkan_INCLUDE_DIR="$WORK/vulkan-headers/include" \
  -DVulkan_LIBRARY="$SYSROOT/usr/lib/aarch64-linux-android/28/libvulkan.so" \
  -DVulkan_GLSLC_EXECUTABLE="$NDK/shader-tools/linux-x86_64/glslc" \
  "-DCMAKE_CXX_FLAGS=-I$WORK/spirv-install/include -I$WORK/vulkan-headers/include"
cmake --build build-android --target llama-server -j "$(( $(nproc) > 2 ? $(nproc) - 2 : 1 ))"

mkdir -p "$(dirname "$OUT")"
"$NDK/toolchains/llvm/prebuilt/linux-x86_64/bin/llvm-strip" -o "$OUT" build-android/bin/llama-server
echo "built $OUT ($(stat -c%s "$OUT") bytes) from llama.cpp $(git -C "$WORK/llama.cpp" rev-parse --short HEAD)"

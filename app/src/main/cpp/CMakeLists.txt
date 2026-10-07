cmake_minimum_required(VERSION 3.22.1)
project(firefly_audio C)

# ---- Codec 2: voice notes small enough for LoRa
file(GLOB CODEC2_SOURCES ${CMAKE_CURRENT_SOURCE_DIR}/codec2/*.c)
add_library(firefly_codec2 SHARED firefly_codec2.c ${CODEC2_SOURCES})
target_include_directories(firefly_codec2 PRIVATE ${CMAKE_CURRENT_SOURCE_DIR}/codec2 ${CMAKE_CURRENT_SOURCE_DIR})
target_compile_options(firefly_codec2 PRIVATE -O2 -Wno-unused-variable -Wno-unused-function)
target_link_libraries(firefly_codec2 m)

# ---- Opus: natural voices where the link allows (libopus' own CMake, static)
set(OPUS_BUILD_TESTING OFF CACHE BOOL "" FORCE)
set(OPUS_BUILD_PROGRAMS OFF CACHE BOOL "" FORCE)
set(OPUS_BUILD_SHARED_LIBRARY OFF CACHE BOOL "" FORCE)
set(OPUS_DRED OFF CACHE BOOL "" FORCE)
set(OPUS_OSCE OFF CACHE BOOL "" FORCE)
set(OPUS_INSTALL_PKG_CONFIG_MODULE OFF CACHE BOOL "" FORCE)
set(OPUS_INSTALL_CMAKE_CONFIG_MODULE OFF CACHE BOOL "" FORCE)
set(CMAKE_POSITION_INDEPENDENT_CODE ON)
add_subdirectory(opus)
# Optimise libopus in every build type: a debug build otherwise compiles it at -O0,
# which makes voice notes several times slower to encode and decode (and opus warns).
# -Wno-cast-align: upstream's x86 AVX2 code (emulator ABI only) uses safe unaligned loads.
target_compile_options(opus PRIVATE -O2 -Wno-cast-align)
add_library(firefly_opus SHARED firefly_opus.c)
target_include_directories(firefly_opus PRIVATE ${CMAKE_CURRENT_SOURCE_DIR}/opus/include)
target_compile_options(firefly_opus PRIVATE -O2)
target_link_libraries(firefly_opus opus m)

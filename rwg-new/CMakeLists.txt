cmake_minimum_required(VERSION 3.15)
project(rwg LANGUAGES C CXX)

set(CMAKE_CXX_STANDARD 20)
set(CMAKE_CXX_STANDARD_REQUIRED ON)

# generate compile_commands.json
set(CMAKE_EXPORT_COMPILE_COMMANDS ON)


set(CMAKE_CXX_FLAGS_RELWITHDEBINFO "-O2 -g -fno-omit-frame-pointer")
set(CMAKE_CXX_FLAGS_DEBUG "-O0 -g -fno-omit-frame-pointer")
set(CMAKE_CXX_FLAGS_RELEASE "-O3 -g -gdwarf-3 -fno-omit-frame-pointer")

# enable hardening
add_compile_definitions(_LIBCPP_HARDENING_MODE=_LIBCPP_HARDENING_MODE_EXTENSIVE)


# Use libc++
set(CMAKE_CXX_FLAGS "${CMAKE_CXX_FLAGS} -stdlib=libc++")
set(CMAKE_EXE_LINKER_FLAGS "${CMAKE_EXE_LINKER_FLAGS} -stdlib=libc++")

# append -Werror=format to CMAKE_CXX_FLAGS
set(CMAKE_CXX_FLAGS "${CMAKE_CXX_FLAGS} -Werror=format -Wshadow -Wnull-dereference -Wuninitialized -Wconversion -Wreorder -Wsign-compare -Wnon-virtual-dtor -Woverloaded-virtual -Wall -Wextra")

# glog
set(glog_DIR ${CMAKE_CURRENT_SOURCE_DIR}/external/glog_install/lib/cmake/glog)
find_package(glog REQUIRED)

# find liburing
find_package(PkgConfig REQUIRED)
pkg_check_modules(LIBURING REQUIRED liburing)

include_directories(include)
file(GLOB_RECURSE SOURCES src/*.cc src/*.c)

add_executable(rwg ${SOURCES})
# link glog
target_link_libraries (rwg glog::glog)
# link liburing
target_link_libraries(rwg ${LIBURING_LIBRARIES})
# link pthread
target_link_libraries(rwg pthread)
# link rt
target_link_libraries(rwg rt)
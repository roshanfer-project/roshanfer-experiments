#include "buffer.hpp"
#include "glog/logging.h"
#include <cstddef>
#include <memory>

Buffer::Buffer(size_t length, size_t id)
    : data(std::vector<char>(length)), is_free(true), is_provided(false),
      size(length - 1), filled(0), index(id) {}

Buffer::~Buffer() {
  LOG(FATAL) << "Buffer deconstructor (should not be called)";
}

void Buffer::clear() {
  filled = 0;
  is_free = true;
}

void Buffer::set_filled(size_t f) {
  if (f > size) {
    LOG(FATAL) << "Buffer overflow, filled: " << f << ", size: " << size;
  }
  filled = f;
}
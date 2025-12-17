#pragma once

#include <cstddef>
#include <memory>
#include <netinet/in.h>
#include <vector>

class Buffer {

public:
  Buffer(size_t, size_t);
  ~Buffer();

  // delete copy semantics
  Buffer(const Buffer &) = delete;
  Buffer &operator=(const Buffer &) = delete;

  // delete move semantics
  Buffer(Buffer &&) = delete;
  Buffer &operator=(Buffer &&) = delete;

  size_t get_size() { return size; }
  size_t get_filled() { return filled; }
  size_t get_index() { return index; }
  void set_filled(size_t f);
  void clear();

public:
  std::vector<char> data;
  bool is_free;
  bool is_provided;

private:
  size_t size;
  size_t filled;
  size_t index;
};
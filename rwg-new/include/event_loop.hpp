#pragma once

#include "rpc_queue.hpp"
#include <buffer_manager.hpp>
#include <listener.hpp>
#include <listener.hpp>
#include <ring_wrapper.hpp>
#include <state.hpp>
#include <string>
#include <unordered_map>

class EventLoop {

public:
  EventLoop(int);
  void run();

public:
  int index;
  RingWrapper ring;
  BufferManager buffer_manager;
  Listener listener;
  RPCQueue rpc_queue;
  State state;
};
#pragma once

#include "buffer.hpp"
#include "buffer_manager.hpp"
#include "config.hpp"
#include "connection.hpp"
#include "connection_enums.hpp"
#include "ppm_queue.hpp"
#include "ring_wrapper.hpp"
#include "rpc_message.hpp"
#include "rpc_queue.hpp"
#include <chrono>
#include <cstddef>
#include <cstdint>
#include <memory>
#include <string>
#include <string_view>
#include <sys/types.h>
#include <unordered_map>
#include <vector>

class AddConnectionException : public std::runtime_error {
public:
  AddConnectionException(std::unique_ptr<HTTPConnection> &ex_conn)
      : std::runtime_error(""), conn(ex_conn) {}

  std::unique_ptr<HTTPConnection> &conn;
};

class ConnectionNotUPException : public std::runtime_error {
public:
  ConnectionNotUPException(std::unique_ptr<HTTPConnection> &ex_conn)
      : std::runtime_error(""), conn(ex_conn) {}

  std::unique_ptr<HTTPConnection> &conn;
};

class UpstreamRouteMapper {
public:
  UpstreamRouteMapper();
  void add_route(std::string);
  ConnectionPool &get_pool(const std::string &);

private:
  std::unordered_map<std::string, ConnectionPool> map;
};

class State {

public:
  State(Config, RingWrapper &, BufferManager &, RPCMapper &, RPCQueue &,
       std::shared_ptr<Listener> &,
        Ingress &, SharedState &, std::string &, int);
  void forward(ConnectionType, ConnectionDirection);
  void remove_connection(std::shared_ptr<HTTPConnection>);

  /*Write request/response from connection's internal state to buffers.
  For HTTP/2 it also writes setting/ping/etc frames.*/
  void write_http(std::shared_ptr<HTTPConnection>);
  bool forward_request(std::shared_ptr<HTTPConnection>,
                       std::shared_ptr<RPCMessage>);
  std::shared_ptr<HTTPConnection> route_request(ConnectionType, int32_t, int);
  void dump_entire_state();

private:
  ConnectionPool ingress_pool;
  UpstreamRouteMapper upstream_route_mapper;
  RingWrapper &ring;
  BufferManager &buffer_manager;
  RPCQueue &rpc_queue;
  std::shared_ptr<Listener>& listener;
  PPMQueue ppm_queue;
  int thread_id;
};
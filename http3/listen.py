import threading, socket
from .libopenssl3 import OpenSSL
from ..listen import HTTPListen
from .connection import HTTPConnectionQUIC

from typing import TYPE_CHECKING
if TYPE_CHECKING:
  from ..interface import HTTPInterface
  from ..serv import HTTPServer



#
# http server listening for incoming QUIC connections endpoint
#  - creates thread which listen and accept incomming clients connections
#
class HTTPListenQUIC(HTTPListen):
  def __init__(self, server: 'HTTPServer', interface: 'HTTPInterface', family: 'socket.AddressFamily', addr: str, port: int):
    super().__init__(server, interface)
    self.access = threading.Lock() # later access to 'self.sockfd'
    self.connections: 'set[HTTPConnectionQUIC]' = set()
    self.shutdown: 'threading.Event | None' = None # if not None shutdowning is in progress, and event is set when there is not more connections

    sock = socket.socket(family, socket.SOCK_DGRAM, socket.IPPROTO_UDP) # UDP "listening" socket
    try:
      if family == socket.AF_INET6:
        sock.setsockopt(socket.IPPROTO_IPV6, socket.IPV6_V6ONLY, 1)

      sock.setsockopt(socket.SOL_SOCKET, socket.SO_REUSEADDR, 1)
      sock.bind((addr, port))

      self.sockfd = sock.detach()

    except Exception:
      sock.close()
      raise

    try:
      self.listener = server._openssl.new_listener(0)

      try:
        if OpenSSL.socket_nbio(self.sockfd, 1) <= 0:
          raise RuntimeError("Failed to set socket to no-blocking mode")

        self.listener.set_fd(self.sockfd)
        self.listener.listen()
      except Exception:
        self.listener.free()
        raise

    except Exception:
      OpenSSL.closesocket(self.sockfd)
      raise


  #
  # start accepting incoming connections
  #
  def start(self):
    if self.thread is None:
      self.thread = threading.Thread(target=self.http_quic_listening_thread, daemon=False)
      self.thread.start()


  #
  # initialize shutdown process
  #
  def stop(self):
    with self.access:
      if self.shutdown is None: # if self.stop() wasn't called before:
        self.shutdown = threading.Event() # event will be set when there is no active connections

        if len(self.connections) == 0:
          self._close_sockfd()
          self.shutdown.set()


  #
  # finalize connection, called from connection thread when connections ends
  #
  def fin_connection(self, conn: 'HTTPConnectionQUIC'):
    self.server._remove_connection(conn)
    conn.free()

    with self.access:
      self.connections.remove(conn)

      if len(self.connections) == 0 and self.shutdown is not None:
        self._close_sockfd()
        self.shutdown.set() # no more connections


  #
  # called some time after stop() to finalize listening process
  #  stop() MUST be called before()
  #
  def join(self):
    if self.shutdown is not None: # if stop() was called:
      self.shutdown.wait() # wait for shutdown all connections

      if self.thread is not None:
        self.thread.join()
        self.thread = None

      if self.listener is not None:
        self.listener.free() # free SSL object
        self.listener = None


  #
  # listening thread entry point
  #
  def http_quic_listening_thread(self) -> None:
    try:
      while True:
        sslconn = self.listener.accept_connection() # will block until new client connection income

        with self.access:
          if sslconn is not None: # if got incoming connection:
            if self.shutdown is None: # if no shutdowning:
              # TODO: get source address of 'conn' client if it's possible, I didn't figure out this yet
              conn = HTTPConnectionQUIC(self, sslconn)
              self.connections.add(conn)
              self.server._start_connection(conn)

            else:
              sslconn.shutdown() # drop connection

          else:
            if self.shutdown is not None: # if stop() called before:
              break # clean exit
            else: # no stop() call before:
              OpenSSL.raise_error("QUIC connections listening failed") # exit by raising error

    except Exception as e:
      try:
        self.server.listen_failed(self.interface)
      except Exception:
        pass

      try:
        self.server.report_exception(e, None)
      except Exception:
        pass

    finally:
      with self.access:
        self._close_sockfd()


  #
  # close listening socket -> breaks http_quic_listening_thread() loop
  #
  def _close_sockfd(self):
    if self.sockfd is not None:
      OpenSSL.closesocket(self.sockfd)
      self.sockfd = None

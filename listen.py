import threading, socket, select, ssl
from .interface import HTTPInterface
from .connection import HTTPConnectionTCP

from typing import TYPE_CHECKING
if TYPE_CHECKING:
  from .serv import HTTPServer


# https://github.com/Pylons/waitress/issues/138
if not hasattr(socket, 'IPPROTO_IPV6'):
  socket.IPPROTO_IPV6 = 41 # missing flag



#
# HTTP incomeing connections listening abstract interface
#
class HTTPListen:
  def __init__(self, server: 'HTTPServer', interface: HTTPInterface):
    self.server = server
    self.interface = interface
    self.thread: 'threading.Thread | None' = None

  #
  # start accepting incoming connections
  #
  def start(self):
    pass

  #
  # initialize stop listening process
  #
  def stop(self):
    pass

  #
  # called some time after stop() to finalize listening process
  #
  def join(self):
    if self.thread is not None:
      self.thread.join()
      self.thread = None





#
# http server listening for TCP connections endpoint
#  - creates thread which listen on socket for incomming clients connections
#
class HTTPListenTCP(HTTPListen):
  def __init__(self, server, interface: HTTPInterface, family: 'socket.AddressFamily', addr: str, port: int, sslctx: 'None | ssl.SSLContext' = None):
    super().__init__(server, interface)
    self.socket = socket.socket(family, socket.SOCK_STREAM) # TCP "listening" socket
    self.shutdown = False

    try:
      if family == socket.AF_INET6:
        self.socket.setsockopt(socket.IPPROTO_IPV6, socket.IPV6_V6ONLY, 1)
      
      self.socket.setsockopt(socket.SOL_SOCKET, socket.SO_REUSEADDR, 1)
      self.socket.bind((addr, port))
      self.socket.listen(128)
      
      if sslctx is not None:
        self.socket = sslctx.wrap_socket(self.socket, server_side=True, do_handshake_on_connect=False)
    
    except Exception:
      self.socket.close()
      raise
  
  
  #
  # start accepting incoming connections
  #
  def start(self) -> None:
    if self.thread is None:
      self.thread = threading.Thread(target=self.http_tcp_listening_thread, daemon=False)
      self.thread.start()


  #
  # initialize stop listening process
  #
  def stop(self) -> None:
    self.shutdown = True


  #
  # listening thread entry point
  #
  def http_tcp_listening_thread(self) -> None:
    try:
      while not self.shutdown:
        ready, _, _ = select.select([ self.socket ], list(), list(), 0.1)
        if self.shutdown:
          break

        if ready: # if got incoming connection
          try:
            sock, addr = self.socket.accept()
          except OSError as e:
            try:
              self.server.listen_failed(self.interface)
            except Exception:
              pass

            try:
              self.server.report_exception(e, None)
            except Exception:
              pass
            break

          self.server._start_connection(HTTPConnectionTCP(self.server, self.interface, sock, addr))
    
    finally:
      try:
        self.socket.close()
      except OSError:
        pass

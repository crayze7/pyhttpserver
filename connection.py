import time, threading, socket, ssl
from .utils import get_thread_id
from .socket_ext import socket_settimeout_recv, socket_settimeout_send
from .interface import HTTPInterface
from .protocol import ProtocolBase

from typing import TYPE_CHECKING
if TYPE_CHECKING:
  from .serv import HTTPServer


#
# HTTP client's connection
#
class HTTPConnection:
  class CLOSE_REASON:
    CLIENT    = 0 # client closed TCP connection
    ABORT     = 1 # TCP connection aborted or dropped
    RECV      = 2 # protocol requested to close by return False in recv()
    CHECK     = 3 # protocol requested to close by return False in check()
    CLOSE_REQ = 4 # protocol requested to close by return False in close_request()
    EXCEPT    = 5 # exception was raised in connection thread
    IDLE      = 6 # close after idle timeout expired


  def __init__(self, server: 'HTTPServer', interface: HTTPInterface, thread: 'threading.Thread'):
    self.server = server
    self.interface = interface
    self.thread = thread
    self.thread_id: int = None


  def start(self):
    self.thread.start()

  def stop(self):
    pass

  def join(self):
    self.thread.join()





#
# HTTP TCP/IP client connection
#
class HTTPConnectionTCP(HTTPConnection):
  def __init__(self, server, interface: HTTPInterface, sock: 'ssl.SSLSocket | socket.socket', addr: 'tuple[str, int]'):
    super().__init__(server, interface, threading.Thread(target=self.http_tcp_connection_thread, daemon=False))
    self.socket = sock # TCP connection socket (may be wrapped by ssl.SSLSocket)
    self.addr = addr # client address: (address, port)
    self.recv_sz = 0 # total number of bytes recived from client on this connection
    self.send_sz = 0 # total number of bytes sent to client on this connection
    self.close_request = False # if True server requested connection to shutdown
    self.cls_error = False # if True connection ends with 'TCP.ERROR' status
    self.cls_args = list()


  #
  # request to close this connection
  #  usually this is called when server started shutdown and class this for all existed connections
  #  somtimes this may be called in other circumstances, eg. stop web socket connection
  #
  def stop(self) -> None:
    self.close_request = True # set requests to close - recv thread checks this value in intervals


  #
  # send bytes stream to the client
  #
  def send(self, stream: bytes) -> bool:
    beg = 0
    end = len(stream)

    while beg < end:
      try:
        sz = self.socket.send(stream[beg:] if beg != 0 else stream)
        beg += sz
        self.send_sz += sz

      except (TimeoutError, BlockingIOError): # if EAGAIN or EWOULDBLOCK
        continue # should never happend, since send has no timeout set

      except (ConnectionAbortedError, ConnectionResetError, BrokenPipeError, ssl.SSLError, ssl.SSLEOFError): # if ECONNRESET or EPIPE
        return False # connection is broken

      except OSError as e: # timeout until python 3.10
        if len(e.args) != 0 and e.args[0] == 'timed out':
          continue # TimeoutError
        else:
          return False # connection is broken

    return True


  #
  # set timeout for socket recv() call
  #  python's socket.settimeout() sets timeout for both recv() and send() calls, but setting timeouts on send() in practice is making troubles,
  #  so socket.settimeout() is just useless garbage, this is implementation of socket.settimeout() for recv() only
  #
  def settimeout_recv(self, timeout: 'float | None'):
    socket_settimeout_recv(self.socket, timeout)

  def settimeout_send(self, timeout: 'float | None'):
    socket_settimeout_send(self.socket, timeout)


  def get_addr(self) -> str:
    return self.addr[0]
  
  def get_port(self) -> int:
    return self.addr[1]


  #
  # connection handling thread
  #
  def http_tcp_connection_thread(self) -> None:
    from .http1 import ProtocolHandlerHTTP1

    http2_enabled = self.server._http2_enable
    if http2_enabled:
      from .http2.handler import ProtocolHandlerHTTP2

    self.thread_id = get_thread_id()
    time_start = time.perf_counter()
    protocol_handler = None
    close_reason = None

    self.server.report_event('TCP.START', self.thread_id, [ self.interface, self.get_addr() ])

    try:
      ok = True
      if isinstance(self.socket, ssl.SSLSocket):
        try: # do SSL handshake:
          self.socket.do_handshake(block=True)

        except OSError as e:
          self.server.report_event('SSL.ERROR', self.thread_id, [ self.get_addr(), str(e) ])
          ok = False

      if ok:
        if http2_enabled and isinstance(self.socket, ssl.SSLSocket) and self.socket.selected_alpn_protocol() == 'h2':
          protocol_handler = ProtocolHandlerHTTP2()
        else:
          protocol_handler = ProtocolHandlerHTTP1()

        self.socket.setsockopt(socket.SOL_SOCKET, socket.SO_KEEPALIVE, 0) # no TCP keep alive
        self.settimeout_recv(0.1) # set recv() timeout on 100ms
        self.settimeout_send(None)

        protocol_handler.use_connection(self) # bind: protocol handler <-> TCP connection
        shutdown_protocol = False
        last_activity = time.perf_counter()

        while True:
          # check continuation status:
          if self.close_request and not shutdown_protocol:
            shutdown_protocol = True
            if not protocol_handler.close_request():
              close_reason = HTTPConnection.CLOSE_REASON.CLOSE_REQ
              break # protocol requested to leave in close_request()

          if not protocol_handler.check():
            close_reason = HTTPConnection.CLOSE_REASON.CHECK
            break # protocol requested to leave in check()

          idle_timeout = protocol_handler.idle_timeout()
          if idle_timeout > 0.0 and time.perf_counter() - last_activity >= idle_timeout: # if timeouted:
            close_reason = HTTPConnection.CLOSE_REASON.IDLE
            break # connection idle timeout

          # try read up to 4KB from TCP socket
          try:
            stream = self.socket.recv(4096)

          except (TimeoutError, BlockingIOError): # EAGAIN, EWOULDBLOCK
            continue

          except (ConnectionAbortedError, ConnectionResetError, ssl.SSLError):
            close_reason = HTTPConnection.CLOSE_REASON.ABORT
            break # connection aborted or dropped

          except OSError as e: # timeout until python 3.10
            if len(e.args) != 0 and e.args[0] == 'timed out':
              continue  # TimeoutError
            else:
              raise

          if not stream:
            close_reason = HTTPConnection.CLOSE_REASON.CLIENT
            break # client just closed TCP connection

          # process stream from TCP socket:
          self.recv_sz += len(stream) # count TCP read size
          result = protocol_handler.recv(stream) # process by protocol handler
          last_activity = time.perf_counter() # update activity on connection

          if isinstance(result, ProtocolBase): # if Switch Protocols:
            protocol_handler.switchby(result) # inform old protocol about switching, may return new protocol name
            protocol_handler = result # exchange protocol instance
            protocol_handler.use_connection(self) # pass connection to new protocol

          elif not result: # if False
            close_reason = HTTPConnection.CLOSE_REASON.RECV
            break # protocol requested to leave in recv()
    
    except Exception as e:
      try:
        self.server.report_exception(e, None)
      except Exception:
        pass

      if protocol_handler is not None:
        close_reason = HTTPConnection.CLOSE_REASON.EXCEPT

      self.cls_error = True
      self.cls_args.append('ERROR="%s"' % (str(e), ))

    self.settimeout_recv(None)

    # close socket reading:
    try:
      self.socket.shutdown(socket.SHUT_RD)
    except OSError:
      pass
    
    if protocol_handler is not None:
      try:
        protocol_handler.close(close_reason)
      except Exception as e:
        try:
          self.server.report_exception(e, None)
        except Exception:
          pass

    # close socket reading and writing:
    try:
      self.socket.shutdown(socket.SHUT_RDWR)
    except OSError:
      pass
    
    # close socket fd
    try:
      self.socket.close()
    except OSError:
      pass
    
    
    duration = time.perf_counter() - time_start
    
    self.server._remove_connection(self)
    
    ev_args = [ self.interface, self.get_addr(), 'time=' + str(duration) ]
    if self.recv_sz > 0:
      ev_args.append('recv=' + str(self.recv_sz))
    if self.send_sz > 0:
      ev_args.append('send=' + str(self.send_sz))
    if len(self.cls_args) > 0:
      ev_args.extend(self.cls_args)
    
    self.server.report_event('TCP.CLOSE' if not self.cls_error else 'TCP.ERROR', self.thread_id, ev_args)

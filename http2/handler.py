
#
# implementation based on RFC 9113
# https://www.rfc-editor.org/rfc/rfc9113.html
#
from .streams import HTTP2StreamsHandler
from .send import HTTP2SendThread
from .parser import HTTP2Parser
from ..protocol import ProtocolBase

from typing import TYPE_CHECKING
if TYPE_CHECKING:
  from ..connection import HTTPConnectionTCP



#
# HTTP/2 protocol handler
#
class ProtocolHandlerHTTP2(ProtocolBase):
  def __init__(self):
    super().__init__()
    self.conn: 'HTTPConnectionTCP' = None # TCP connection to use
    self.streams: 'HTTP2StreamsHandler' = None # streams handler
    self.send: 'HTTP2SendThread' = None # sending queue and thread
    self.parser: 'HTTP2Parser' = None # parsing income HTTP/2 protocol bytes runtime


  #
  # @ProtocolBase method
  # assigning TCP connection with this instance and create processing objects
  #
  def use_connection(self, conn: 'HTTPConnectionTCP'):
    self.conn = conn
    self.streams = HTTP2StreamsHandler(self)
    self.send = HTTP2SendThread(self)
    self.parser = HTTP2Parser(self)


  #
  # @ProtocolBase method
  # called when server requested to close the connection
  #
  def close_request(self) -> bool:
    self.streams.connection_close_request()
    return False


  #
  # @ProtocolBase method
  # check called about every period of time (check ProtocolBase), can be used to any maintance:
  #  in HTTP2 this method is used to check if sending thread is still working
  #  if not (connection dropped or exception in sending thread) stop receiving and continue in close()
  #
  def check(self) -> bool:
    if not self.send.is_running():
      return False # sending thread stopped

    self.streams.check()
    return True


  #
  # called by connection on incoming TCP stream bytes
  #
  def recv(self, stream: bytes) -> bool:
    return self.parser.recv(stream)


  #
  # called when connection is closing, right before close the connection socket
  #
  def close(self, close_reason: int) -> None:
    self.streams.connection_close(close_reason)

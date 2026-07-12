import threading
from .libopenssl3 import OpenSSL
from .parser import HTTP3Parser, ERROR_CODE
from .handler import HTTP3HandlerUni, HTTP3HandlerStreamRequest

from typing import TYPE_CHECKING
if TYPE_CHECKING:
  from .parser import HTTP3ParserDiscard
  from .connection import HTTPConnectionQUIC



#
# QUIC income stream (created by the client)
#  that stream has active READING
#
class HTTPStreamQUIC:
  def __init__(self, conn: 'HTTPConnectionQUIC', sslstream: 'OpenSSL.SSLStream', quicid: int, bidirectional: bool):
    self.conn = conn # creator: QUIC connection
    self.sslstream = sslstream # SSL* stream instance
    self.quicid = quicid
    self.bidirectional = bidirectional # if True stream represents HTTP request->response pattern

    self.thread = threading.Thread(target=self.http_quic_stream_thread, daemon=False)
    self.parser: 'HTTP3Parser' = None
    self.rst_code: 'int | None' = None # if not None stream reset called

  @property
  def shutdown(self):
    return self.conn.shutdown is not None


  #
  #  QUIC stream handling thread
  #
  def http_quic_stream_thread(self):
    self.parser: 'HTTP3Parser | HTTP3ParserDiscard' = HTTP3Parser(HTTP3HandlerStreamRequest(self) if self.bidirectional else HTTP3HandlerUni(self), self.bidirectional)
    ok = True

    try:
      while True: # stream reading stage:
        data = self.sslstream.read(4096) # try read up to 4KB from QUIC stream

        if data:
          if not self.parser.recv(data): # consume income data by: parser -> protocol handler
            ok = False
            break

        else: # if no more income 'data':
          if self.parser.end(): # if message completed/succeeded:
            self.parser.h.process() # processing/sending state
          else:
            ok = False
          break

    except OpenSSL.Disconnected: # client closed stream
      ok = False

    except Exception as e:
      ok = False
      if not self.shutdown:
        try:
          self.conn.server.report_exception(e, None)
        except Exception:
          pass

    if self.bidirectional:
      if ok:
        self.sslstream.stream_conclude(0)
      else:
        self.stream_error(ERROR_CODE.GENERAL_PROTOCOL_ERROR)

    self.conn.fin_stream(self)



  #
  # called on stream error occurance
  #
  def stream_error(self, error_code: int):
    if self.rst_code is None:
      self.rst_code = error_code

      args = OpenSSL.STREAM_RESET_ARGS()
      args.quic_error_code = error_code
      self.sslstream.stream_reset(args)


  #
  # called on connection error occurance
  #
  def connection_error(self, error_code: int):
    self.conn.connection_error(error_code)

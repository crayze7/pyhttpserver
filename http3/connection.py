import threading
from ..utils import get_thread_id
from .libopenssl3 import OpenSSL
from .parser import ERROR_CODE, SETTINGS, MakeFrame
from .stream import HTTPStreamQUIC
from ..connection import HTTPConnection

from typing import TYPE_CHECKING
if TYPE_CHECKING:
  from .listen import HTTPListenQUIC


SERVER_QPACK_MAX_TABLE_CAPACITY = 4096
SERVER_MAX_FIELD_SECTION_SIZE = 4611686018427387903


#
# HTTP UDP/QUIC client connection
#
class HTTPConnectionQUIC(HTTPConnection):
  #
  # connections streams by types
  #
  class Streams:
    #
    # server initiated undirectional (write only) streams
    #
    class Server:
      def __init__(self):
        self.control: 'OpenSSL.SSLStream | None' = None
        self.qpack_decoder: 'OpenSSL.SSLStream | None' = None
        self.qpack_encoder: 'OpenSSL.SSLStream | None' = None

    #
    # client initiated undirectional (read only) streams
    #
    class Client:
      def __init__(self):
        self.control: 'HTTPStreamQUIC | None' = None
        self.qpack_encoder: 'HTTPStreamQUIC | None' = None
        self.qpack_decoder: 'HTTPStreamQUIC | None' = None


    def __init__(self):
      self.income: 'set[HTTPStreamQUIC]' = set() # all income client streams (bidirectional and undirectional)
      self.cli = HTTPConnectionQUIC.Streams.Client() # some unidirectional streams from 'income'
      self.serv = HTTPConnectionQUIC.Streams.Server()



  def __init__(self, listener: 'HTTPListenQUIC', sslconn: 'OpenSSL.SSLConn'):
    super().__init__(listener.server, listener.interface, threading.Thread(target=self.http_quic_connection_thread, daemon=False))
    self.listener = listener # creator: QUIC listener
    self.sslconn = sslconn # SSL QUIC connection

    self.access = threading.Lock()
    self.streams = HTTPConnectionQUIC.Streams()
    self.last_quicid: int = 0
    self.shutdown: 'threading.Event | None' = None

    # connection QPACK decoding & encoding:
    import pylsqpack
    self.EncoderStreamError = pylsqpack.EncoderStreamError
    self.DecoderStreamError = pylsqpack.DecoderStreamError
    self.DecompressionFailed = pylsqpack.DecompressionFailed
    self.StreamBlocked = pylsqpack.StreamBlocked

    self.qpack_decoder_access = threading.Lock()
    self.qpack_decoder = pylsqpack.Decoder(SERVER_QPACK_MAX_TABLE_CAPACITY, 0) # decoder of data encoded by the client

    self.qpack_encoder_access = threading.Lock()
    self.qpack_encoder = pylsqpack.Encoder() # encoder of data encoded by this server

    # client's settings:
    self.MAX_PUSH_ID = -1


  def free(self):
    if self.streams.serv.control is not None:
      self.streams.serv.control.stream_conclude(0)
      self.streams.serv.control.free()

    if self.streams.serv.qpack_encoder is not None:
      self.streams.serv.qpack_encoder.stream_conclude(0)
      self.streams.serv.qpack_encoder.free()
      self.streams.serv.qpack_encoder = None

    if self.streams.serv.qpack_decoder is not None:
      self.streams.serv.qpack_decoder.stream_conclude(0)
      self.streams.serv.qpack_decoder.free()
      self.streams.serv.qpack_decoder = None

    self.sslconn.free()


  #
  # stop connection on demand
  #  called from HTTPListenQUIC.stop()
  #
  def stop(self):
    with self.access:
      if self.shutdown is not None:
        return

      self.shutdown = threading.Event()

      if len(self.streams.income) != 0:
        for stream in self.streams.income:
          if stream.rst_code is None:
            stream.rst_code = 0
            stream.sslstream.stream_reset()

        self.sslconn.shutdown() # shutdown QUIC connection...

      else:
        self.sslconn.shutdown() # shutdown QUIC connection...
        self.shutdown.set()
        return # no need to call wait()

    self.shutdown.wait() # wait for fin_stream()


  #
  # finalize stream, called from stream thread
  #
  def fin_stream(self, stream: 'HTTPStreamQUIC'):
    stream.sslstream.free()

    with self.access:
      self.streams.income.remove(stream)

      if len(self.streams.income) == 0 and self.shutdown: # if no more streams and shutdown is in progress:
        self.shutdown.set()



  #
  # connection handling thread
  #
  def http_quic_connection_thread(self):
    self.thread_id = get_thread_id()

    try:
      self.sslconn.set_default_stream_mode(OpenSSL.SSL_DEFAULT_STREAM_MODE_NONE)
      self.sslconn.set_incoming_stream_policy(OpenSSL.SSL_INCOMING_STREAM_POLICY_ACCEPT, 0)

      # open control stream from the server:
      self.streams.serv.control = self.sslconn.new_stream(OpenSSL.SSL_STREAM_FLAG_UNI | OpenSSL.SSL_STREAM_FLAG_ADVANCE)
      self.streams.serv.control.write(b'\x00' + MakeFrame.settings([
        (SETTINGS.MAX_FIELD_SECTION_SIZE, SERVER_MAX_FIELD_SECTION_SIZE),
        (SETTINGS.QPACK_MAX_TABLE_CAPACITY, SERVER_QPACK_MAX_TABLE_CAPACITY),
        (SETTINGS.QPACK_BLOCKED_STREAMS, 0)
      ]))

      # open encoder stream from the server:
      self.streams.serv.qpack_encoder = self.sslconn.new_stream(OpenSSL.SSL_STREAM_FLAG_UNI | OpenSSL.SSL_STREAM_FLAG_ADVANCE)
      self.streams.serv.qpack_encoder.write(b'\x02')

      # open decoder stream ftom the server:
      self.streams.serv.qpack_decoder = self.sslconn.new_stream(OpenSSL.SSL_STREAM_FLAG_UNI | OpenSSL.SSL_STREAM_FLAG_ADVANCE)
      self.streams.serv.qpack_decoder.write(b'\x03')

      while True:
        sslstream = self.sslconn.accept_stream() # wait for income client's stream

        with self.access:
          if sslstream is not None and (sslstream.get_stream_type() & OpenSSL.SSL_STREAM_TYPE_READ) != 0: # stream must be readable
            if not self.shutdown: # allow new streams:
              quicid = sslstream.get_stream_id() # QUIC stream ID
              bidirectional = (sslstream.get_stream_type() & OpenSSL.SSL_STREAM_TYPE_WRITE) != 0
              self.last_quicid = max(self.last_quicid, quicid) # last highest client stream

              stream = HTTPStreamQUIC(self, sslstream, quicid, bidirectional)
              self.streams.income.add(stream)
              stream.thread.start() # start new thread to process QUIC stream

            else: # disallow new streams or stream isn't bidirectional (HTTP/3 works only on BIDIR QUIC streams):
              sslstream.stream_reset()
              sslstream.free()

          else: # connection terminated:
            break # clean exit

    except Exception as e:
      try:
        self.server.report_exception(e, None)
      except Exception:
        pass

    self.listener.fin_connection(self)


  #
  # assigning connection control stream from the client
  #
  def assigning_control_stream(self, stream: 'HTTPStreamQUIC'):
    with self.access:
      if self.streams.cli.control is None:
        self.streams.cli.control = stream
        return True

    self.connection_error(ERROR_CODE.STREAM_CREATION_ERROR)
    return False

  #
  # assigning QPACK encoder stream from the client
  #
  def assigning_encoder_stream(self, stream: 'HTTPStreamQUIC'):
    with self.access:
      if self.streams.cli.qpack_encoder is None:
        self.streams.cli.qpack_encoder = stream
        return True

    self.connection_error(ERROR_CODE.STREAM_CREATION_ERROR)
    return False

  #
  # assigning QPACK decoder stream from the client
  #
  def assigning_decoder_stream(self, stream: 'HTTPStreamQUIC'):
    with self.access:
      if self.streams.cli.qpack_decoder is None:
        self.streams.cli.qpack_decoder = stream
        return True

    self.connection_error(ERROR_CODE.STREAM_CREATION_ERROR)
    return False


  #
  # called on connection error
  #
  def connection_error(self, error_code: int):
    args = OpenSSL.SSL_SHUTDOWN_EX_ARGS()
    args.quic_error_code = error_code
    args.quic_reason = None
    self.sslconn.shutdown_ex(0, args)

    self.stop()

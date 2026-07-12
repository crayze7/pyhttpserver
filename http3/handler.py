import time
from ..utils import get_thread_id
from .parser import ERROR_CODE, SETTINGS, HTTP3ParserDiscard, HTTP3ParserHandler, QPACKParserEncoder, QPACKParserDecoder, MakeFrame
from ..status import HTTPStatus
from ..request import HTTPRequest
from ..respond import HTTPRespond
from ..http2.stream import HTTP2Stream

from typing import TYPE_CHECKING
if TYPE_CHECKING:
  from .stream import HTTPStreamQUIC



class HTTP3HandlerStream(HTTP3ParserHandler):
  def __init__(self, stream: 'HTTPStreamQUIC'):
    super().__init__()
    self.stream = stream

  def connection_error(self, error_code: int):
    self.stream.connection_error(error_code)

  def stream_error(self, error_code: int):
    self.stream.stream_error(error_code)



#
# unknown type unidirectional QUIC stream parsed frames handler
#
class HTTP3HandlerUni(HTTP3HandlerStream):
  #
  # got control stream
  #
  def handle_stream_control(self):
    self.stream.parser.h = HTTP3HandlerStreamControl(self.stream) # upgrade handler type: HTTP3HandlerUni -> HTTP3HandlerStreamControl
    return self.stream.conn.assigning_control_stream(self.stream)

  #
  # got push stream from the client
  #
  def handle_stream_push(self, _: int):
    # "Only servers can push; if a server receives a client-initiated push stream, this MUST be treated as a connection error of type H3_STREAM_CREATION_ERROR."
    self.connection_error(ERROR_CODE.STREAM_CREATION_ERROR)
    return False

  #
  # got QPACK encoder stream from the client
  #
  def handle_stream_qpack_encoder(self, instructions: bytes):
    self.stream.parser = QPACKParserEncoder(self.stream.conn)
    if not self.stream.conn.assigning_encoder_stream(self.stream):
      return False

    return self.stream.parser.recv(instructions) if len(instructions) != 0 else True

  #
  # got QPACK decoder stream from the client
  #
  def handle_stream_qpack_decoder(self, instructions: bytes):
    self.stream.parser = QPACKParserDecoder(self.stream.conn)
    if not self.stream.conn.assigning_decoder_stream(self.stream):
      return False

    return self.stream.parser.recv(instructions) if len(instructions) != 0 else True

  #
  # got unknown stream type
  #
  def handle_stream_unknown(self, _: int):
    self.stream.parser = HTTP3ParserDiscard() # parser without handler that discards all income data
    return True





#
# QUIC connection control stream parsed frames handler
#
class HTTP3HandlerStreamControl(HTTP3HandlerStream):
  def __init__(self, stream: 'HTTPStreamQUIC'):
    super().__init__(stream)
    self.got_settings = False # recieved SETTINGS frame, which must be the first frame in this stream


  #
  # process SETTINGS frame
  #
  def handle_frame_settings(self, options: 'list[tuple[int, int]]'):
    if self.got_settings: # 2nd SETTINGS frame is disallowed:
      self.connection_error(ERROR_CODE.FRAME_UNEXPECTED)
      return False

    prevs: 'set[int]' = set()
    for opt in options:
      key = opt[0]
      MAX_TABLE_CAPACITY = 0
      BLOCKED_STREAMS = 0

      if key in prevs:
        self.connection_error(ERROR_CODE.SETTINGS_ERROR)
        return False

      prevs.add(key)

      if key == SETTINGS.QPACK_MAX_TABLE_CAPACITY:
        MAX_TABLE_CAPACITY = opt[1]
      elif key == SETTINGS.QPACK_BLOCKED_STREAMS:
        BLOCKED_STREAMS = opt[1]

    with self.stream.conn.qpack_encoder_access:
      self.stream.conn.qpack_encoder.apply_settings(MAX_TABLE_CAPACITY, BLOCKED_STREAMS)

    self.got_settings = True
    return True


  #
  # process GOAWAY frame from the client
  #
  def handle_frame_goaway(self, stream_push_id: int):
    if not self.got_settings:
      self.connection_error(ERROR_CODE.MISSING_SETTINGS)
      return False

    self.stream.conn.streams.serv.control.write(MakeFrame.goaway(self.stream.conn.last_quicid))
    self.stream.conn.stop()
    return True


  #
  # process MAX_PUSH_ID frame
  #
  def handle_frame_max_push_id(self, push_id: int):
    if not self.got_settings:
      self.connection_error(ERROR_CODE.MISSING_SETTINGS)
      return False

    if push_id < self.stream.conn.MAX_PUSH_ID:
      self.connection_error(ERROR_CODE.ID_ERROR)
      return False

    self.stream.conn.MAX_PUSH_ID = push_id
    return True


  #
  # process CANCEL_PUSH
  #
  def handle_frame_cancel_push(self, push_id: int):
    if not self.got_settings:
      self.connection_error(ERROR_CODE.MISSING_SETTINGS)
      return False

    if push_id > self.stream.conn.MAX_PUSH_ID:
      self.connection_error(ERROR_CODE.ID_ERROR)
      return False

    return True # server does not support making push requests, so there is nothing to cancel...


  def handle_frame_headers(self, _: bytes):
    self.connection_error(ERROR_CODE.FRAME_UNEXPECTED)
    return False # HEADERS not allowed on control stream

  def handle_frame_data(self, _: bytes):
    self.connection_error(ERROR_CODE.FRAME_UNEXPECTED)
    return False # DATA not allowed on control stream

  def handle_frame_push_promise(self, _: int, __: bytes):
    self.connection_error(ERROR_CODE.FRAME_UNEXPECTED)
    return False # client never send PUSH_PROMISE





#
# handling request stream frames
#  only allowed frames on that stream sent by the client are:
#   single HEADERS frame + optional DATA frames + optional HEADERS frame
#   PUSH_PROMISE frames here are not supported by this implemntation
#
class HTTP3HandlerStreamRequest(HTTP3HandlerStream):
  def __init__(self, stream: 'HTTPStreamQUIC'):
    super().__init__(stream)
    self.header: 'bytes | None' = None
    self.data: 'list[bytes] | None' = list()
    self.trailer: 'bytes | None' = None

    self.request: 'HTTPRequest | None' = None # writing in 'recv thead', readonly in 'processing thread'
    self.respond: 'HTTPRespond | None' = None # writing in 'processing thread', readonly in 'sending thread'


  def handle_frame_headers(self, efs: bytes):
    if self.header is None:
      self.header = efs
      return True

    if self.trailer is None:
      self.trailer = efs
      return True

    self.connection_error(ERROR_CODE.FRAME_UNEXPECTED)
    return False


  def handle_frame_data(self, data: bytes):
    if self.header is None or self.trailer is not None:
      self.connection_error(ERROR_CODE.FRAME_UNEXPECTED)
      return False

    self.data.append(data)
    return True


  #
  # completing request ends
  #
  def end(self):
    if self.header is None: # if no HEADERS sent:
      self.connection_error(ERROR_CODE.FRAME_UNEXPECTED)
      return False

    self.request = HTTPRequest(self.stream.conn.interface, '', 'HTTP/3')

    body = bytes().join(self.data)
    self.data = None
    self.request.body = body if len(body) != 0 else None

    try:
      with self.stream.conn.qpack_decoder_access:
        res: 'tuple[bytes, list[tuple[bytes, bytes]]]' = self.stream.conn.qpack_decoder.feed_header(self.stream.quicid, self.header)

    except (self.stream.conn.DecompressionFailed, self.stream.conn.StreamBlocked):
      self.connection_error(ERROR_CODE.QPACK_DECOMPRESSION_FAILED)
      return False

    controldata = res[0]
    if len(controldata) != 0:
      self.stream.conn.streams.serv.qpack_decoder.write(controldata)

    # set request headers:
    for token, value in res[1]:
      name = token.lower().decode('ascii')
      value = value.decode('iso-8859-1')

      if name.startswith(':'):
        if name == ':method':
          self.request.method = value
        elif name == ':authority':
          self.request.host = value
        elif name == ':path':
          self.request.setUrl(value)

      elif name == 'host':
        if self.request.host is None: # :authority has higher priority
          self.request.host = value

      elif name != 'cookie':
        self.request.headers.set(token, value)

      else: # process cookie header (RFC 9113 8.2.3 - HTTP/2 may contain multiple 'cookie' headers):
        if ';' in value: # if header with multiple values:
          for cookie in value.split(';'):
            pair = cookie.split('=')
            if len(pair) == 2:
              self.request.cookies[pair[0].lstrip()] = pair[1]
        else: # if header with single cookie:
          pair = value.split('=')
          if len(pair) == 2:
            self.request.cookies[pair[0]] = pair[1]

    if not self.request.method or not self.request.url:
      self.stream_error(ERROR_CODE.MESSAGE_ERROR)
      return False

    return True



  #
  # processing reponse
  #
  def process(self):
    server = self.stream.conn.server
    respond = HTTPRespond()
    self.respond = respond

    if self.request.method in ('GET', 'HEAD', 'POST', 'PUT', 'DELETE', 'OPTIONS', 'PATCH'):
      self.log_report_start()
      start_time = time.perf_counter()

      try:
        server.process_request(self.request, respond) # processing the request
        respond.postprocess(self.request, server.conf_body_encoding()) # postprocessing the respond
        duration = time.perf_counter() - start_time

      except Exception as e:
        duration = time.perf_counter() - start_time

        respond.reset() # reset respond
        respond.status_code = HTTPStatus.INTERNAL_SERVER_ERROR
        try:
          server.report_exception(e, self.request)
        except Exception:
          pass

      body_size = len(respond.body) if respond.body else 0
      send_body_size = body_size if self.request.method != 'HEAD' else 0 # same as 'body_size' or 0 if 'HEAD' request

      self.log_report_response(duration, send_body_size)

    else:
      respond.status_code  = HTTPStatus.METHOD_NOT_ALLOWED
      body_size = 0
      send_body_size = 0

    # prepare HTTP/3 headers to encode (they works the same like in HTTP/2):
    qpack_headers = HTTP2Stream.make_respond_headers(server, respond, body_size, 3)

    if send_body_size == 0 and respond.body:
      respond.body = None # discard body

    with self.stream.conn.qpack_encoder_access:
      res: 'tuple[bytes, bytes]' = self.stream.conn.qpack_encoder.encode(self.stream.quicid, qpack_headers)

    controldata = res[0]
    if len(controldata) != 0:
      self.stream.conn.streams.serv.qpack_encoder.write(controldata)

    self.stream.sslstream.write(MakeFrame.headers(res[1]))
    if send_body_size != 0:
      self.stream.sslstream.write(MakeFrame.data(respond.body))



  #
  # report start request in log
  # called from 'processing thread'
  #
  def log_report_start(self):
    ev_args = [ '"%s %s"' % ( self.request.method, self.request.url ) ] # format HTTP1 like request pseudo line
    if self.request.getBodySize() > 0:
      ev_args.append('body=' + str(self.request.getBodySize()))

    self.stream.conn.server.report_event('HTTP3.REQ', get_thread_id(), ev_args)

  #
  # report start respponse in log
  # called from 'processing thread'
  #
  def log_report_response(self, duration: int, send_body_size: int):
    reqline = '"%s %s"' % ( self.request.method, self.request.url ) # format HTTP1 like request pseudo line
    ev_args = [ reqline, self.respond.status_code ]

    if send_body_size > 0:
      ev_args.append('body=' + str(send_body_size))

    ev_args.append('time=' + str(duration))

    self.stream.conn.server.report_event('HTTP3.RES', get_thread_id(), ev_args)





  def handle_frame_settings(self, _: 'list[tuple[int, int]]'):
    self.connection_error(ERROR_CODE.FRAME_UNEXPECTED)
    return False

  def handle_frame_goaway(self, _: int):
    self.connection_error(ERROR_CODE.FRAME_UNEXPECTED)
    return False

  def handle_frame_max_push_id(self, _: int):
    self.connection_error(ERROR_CODE.FRAME_UNEXPECTED)
    return False
  
  def handle_frame_cancel_push(self, _: int):
    self.connection_error(ERROR_CODE.FRAME_UNEXPECTED)
    return False

  def handle_frame_push_promise(self, _: int, __: bytes):
    self.connection_error(ERROR_CODE.FRAME_UNEXPECTED)
    return False

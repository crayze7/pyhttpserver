from typing import TYPE_CHECKING
if TYPE_CHECKING:
  from .connection import HTTPConnectionQUIC


#
# RFC 9114 8.1
#
class ERROR_CODE:
  NO_ERROR = 0x0100 # No error. This is used when the connection or stream needs to be closed, but there is no error to signal.
  GENERAL_PROTOCOL_ERROR = 0x0101 # Peer violated protocol requirements in a way that does not match a more specific error code or endpoint declines to use the more specific error code.
  INTERNAL_ERROR = 0x0102 # An internal error has occurred in the HTTP stack.
  STREAM_CREATION_ERROR = 0x0103 # The endpoint detected that its peer created a stream that it will not accept.
  CLOSED_CRITICAL_STREAM = 0x0104 # A stream required by the HTTP/3 connection was closed or reset.
  FRAME_UNEXPECTED = 0x0105 # A frame was received that was not permitted in the current state or on the current stream.
  FRAME_ERROR = 0x0106 # A frame that fails to satisfy layout requirements or with an invalid size was received.
  EXCESSIVE_LOAD = 0x0107 # The endpoint detected that its peer is exhibiting a behavior that might be generating excessive load.
  ID_ERROR = 0x0108 # A stream ID or push ID was used incorrectly, such as exceeding a limit, reducing a limit, or being reused.
  SETTINGS_ERROR = 0x0109 # An endpoint detected an error in the payload of a SETTINGS frame.
  MISSING_SETTINGS = 0x010a # No SETTINGS frame was received at the beginning of the control stream.
  REQUEST_REJECTED = 0x010b # A server rejected a request without performing any application processing.
  REQUEST_CANCELLED = 0x010c # The request or its response (including pushed response) is cancelled.
  REQUEST_INCOMPLETE = 0x010d # The client's stream terminated without containing a fully formed request.
  MESSAGE_ERROR = 0x010e # An HTTP message was malformed and cannot be processed.
  CONNECT_ERROR = 0x010f # The TCP connection established in response to a CONNECT request was reset or abnormally closed.
  VERSION_FALLBACK = 0x0110 # The requested operation cannot be served over HTTP/3. The peer should retry over HTTP/1.1.

  # RFC 9204 6.
  QPACK_DECOMPRESSION_FAILED = 0x0200 # The decoder failed to interpret an encoded field section and is not able to continue decoding that field section.
  QPACK_ENCODER_STREAM_ERROR = 0x0201 # The decoder failed to interpret an encoder instruction received on the encoder stream.
  QPACK_DECODER_STREAM_ERROR = 0x0202 # The encoder failed to interpret a decoder instruction received on the decoder stream.


class FRAME_TYPE:
  DATA = 0x00
  HEADERS = 0x01
  CANCEL_PUSH = 0x03
  SETTINGS = 0x04
  PUSH_PROMISE = 0x05
  GOAWAY = 0x07
  MAX_PUSH_ID = 0x0d


class SETTINGS:
  MAX_FIELD_SECTION_SIZE = 0x06
  QPACK_MAX_TABLE_CAPACITY = 0x01
  QPACK_BLOCKED_STREAMS = 0x07



#
# make HTTP/3 frame as single memory block
#
class MakeFrame:
  @staticmethod
  def make(frame_id: int, payload: bytes):
    return bytes().join([ varlenint.encode(frame_id), varlenint.encode(len(payload)), payload ])


  @staticmethod
  def headers(efs: bytes):
    return MakeFrame.make(FRAME_TYPE.HEADERS, efs)

  @staticmethod
  def data(data: bytes):
    return MakeFrame.make(FRAME_TYPE.DATA, data)


  @staticmethod
  def goaway(stream_push_id: int):
    return MakeFrame.make(FRAME_TYPE.GOAWAY, varlenint.encode(stream_push_id))

  @staticmethod
  def settings(options: 'list[tuple[int, int]]'):
    payload: 'list[bytes]' = []
    for opt in options:
      payload.append(varlenint.encode(opt[0]))
      payload.append(varlenint.encode(opt[1]))

    return MakeFrame.make(FRAME_TYPE.SETTINGS, bytes().join(payload))







class HTTP3ParserHandler:
  def end(self) -> bool:
    return True
  
  def process(self):
    pass


  def connection_error(self, error_code: int):
    pass

  def stream_error(self, error_code: int):
    pass


  def handle_stream_control(self) -> bool: # got control stream
    return True

  def handle_stream_push(self, push_id: int) -> bool: # got server push stream
    return True

  def handle_stream_qpack_encoder(self, instructions: bytes) -> bool: # got QPACK encoder stream
    return True

  def handle_stream_qpack_decoder(self, instructions: bytes) -> bool: # got QPACK decoder stream
    return True

  def handle_stream_unknown(self, stream_type: int) -> bool: # got unknown stream type
    return True


  def handle_frame_headers(self, efs: bytes) -> bool:
    return True

  def handle_frame_data(self, data: bytes) -> bool:
    return True

  def handle_frame_cancel_push(self, push_id: int) -> bool:
    return True

  def handle_frame_settings(self, options: 'list[tuple[int, int]]') -> bool:
    return True

  def handle_frame_push_promise(self, push_id: int, efs: bytes) -> bool:
    return True

  def handle_frame_goaway(self, stream_push_id: int) -> bool:
    return True

  def handle_frame_max_push_id(self, push_id: int) -> bool:
    return True



#
# parser that discards data - used with unknown stream
#
class HTTP3ParserDiscard:
  def recv(self, _: bytes):
    return True

  def end(self):
    return False



#
# parsing income HTTP/3 bytes as frames
#  RFC 9114 7.1
#
class HTTP3Parser:
  class STATE:
    FRAME_TYPE = 0
    FRAME_LENGTH = 1
    FRAME_PAYLOAD = 2
    STREAM_TYPE = -1 # unidirectional stream start with stream type (0=control stream, 1=server push stream)
    STREAM_PUSH_ID = -2


  def __init__(self, handler: 'HTTP3ParserHandler', bidirectional: bool):
    self.h = handler
    self.cache = bytes()
    self.state = HTTP3Parser.STATE.FRAME_TYPE if bidirectional else HTTP3Parser.STATE.STREAM_TYPE
    self.frame_type = None
    self.payload_size = None


  def recv(self, stream: bytes):
    self.cache += stream

    while True:
      if self.state == HTTP3Parser.STATE.FRAME_TYPE:
        self.frame_type, length = varlenint.decode(self.cache, 0)
        if self.frame_type is None:
          return True # need more data

        self.state = HTTP3Parser.STATE.FRAME_LENGTH
        self.cache = self.cache[length:]


      elif self.state == HTTP3Parser.STATE.FRAME_LENGTH:
        self.payload_size, length = varlenint.decode(self.cache, 0)
        if self.payload_size is None:
          return True # need more data

        self.state = HTTP3Parser.STATE.FRAME_PAYLOAD
        self.cache = self.cache[length:]


      elif self.state == HTTP3Parser.STATE.FRAME_PAYLOAD:
        if len(self.cache) < self.payload_size:
          return True # need more data

        frame_type = self.frame_type
        payload = self.cache[:self.payload_size]

        self.cache = self.cache[self.payload_size:]
        self.frame_type = None
        self.payload_size = None
        self.state = HTTP3Parser.STATE.FRAME_TYPE

        if not self.handle_frame(frame_type, payload):
          return False

      elif self.state == HTTP3Parser.STATE.STREAM_TYPE:
        stream_type, length = varlenint.decode(self.cache, 0)
        if stream_type is None:
          return True # need more data

        if stream_type == 2: # if QPACK encoder stream:
          return self.h.handle_stream_qpack_encoder(self.cache[length:])

        if stream_type == 3: # if QPACK decoder stream:
          return self.h.handle_stream_qpack_decoder(self.cache[length:])

        if stream_type == 0: # if control stream:
          self.state = HTTP3Parser.STATE.FRAME_TYPE
          self.cache = self.cache[length:]

          if not self.h.handle_stream_control():
            return False

        elif stream_type == 1: # if push stream:
          self.state = HTTP3Parser.STATE.STREAM_PUSH_ID
          self.cache = self.cache[length:]

        else: # if unknown stream type:
          return self.h.handle_stream_unknown(stream_type)

      else: # if self.state == HTTP3Parser.STATE.STREAM_PUSH_ID:
        push_id, length = varlenint.decode(self.cache, 0)
        if push_id is None:
          return True # need more data

        if not self.h.handle_stream_push(push_id):
          return False

        self.state = HTTP3Parser.STATE.FRAME_TYPE
        self.cache = self.cache[length:]


  #
  # called when frame is complete
  #
  def handle_frame(self, frame_type: int, payload: bytes):
    if frame_type == FRAME_TYPE.DATA: # 7.2.1 DATA
      return self.h.handle_frame_data(payload)


    if frame_type == FRAME_TYPE.HEADERS: # 7.2.2 HEADERS
      return self.h.handle_frame_headers(payload)


    if frame_type == FRAME_TYPE.CANCEL_PUSH: # 7.2.3 CANCEL_PUSH
      push_id, length = varlenint.decode(payload, 0)
      if push_id is None or length != len(payload):
        self.h.connection_error(ERROR_CODE.FRAME_ERROR)
        return False

      return self.h.handle_frame_cancel_push(push_id)


    if frame_type == FRAME_TYPE.SETTINGS: # 7.2.4 SETTINGS
      options: 'list[tuple[int, int]]' = list()
      i = 0

      while i < len(payload):
        id, i = varlenint.decode(payload, i)
        if id is None:
          self.h.connection_error(ERROR_CODE.FRAME_ERROR)
          return False

        value, i = varlenint.decode(payload, i)
        if value is None:
          self.h.connection_error(ERROR_CODE.FRAME_ERROR)
          return False

        options.append((id, value))

      return self.h.handle_frame_settings(options)


    if frame_type == FRAME_TYPE.PUSH_PROMISE:
      push_id, length = varlenint.decode(payload, 0)
      if push_id is None:
        self.h.connection_error(ERROR_CODE.FRAME_ERROR)
        return False

      return self.h.handle_frame_push_promise(push_id, payload[length:])


    if frame_type == FRAME_TYPE.GOAWAY:
      stream_push_id, length = varlenint.decode(payload, 0)
      if stream_push_id is None or length != len(payload):
        self.h.connection_error(ERROR_CODE.FRAME_ERROR)
        return False

      return self.h.handle_frame_goaway(stream_push_id)


    if frame_type == FRAME_TYPE.MAX_PUSH_ID:
      push_id, length = varlenint.decode(payload, 0)
      if push_id is None or length != len(payload):
        self.h.connection_error(ERROR_CODE.FRAME_ERROR)
        return False

      return self.h.handle_frame_max_push_id(push_id)


    return True


  #
  # end of the recv message from the client on that stream
  #
  def end(self):
    if self.state != HTTP3Parser.STATE.FRAME_TYPE or len(self.cache) != 0:
      self.h.stream_error(ERROR_CODE.REQUEST_INCOMPLETE)
      return False

    return self.h.end()







#
# QPack instructions parser
#  https://www.rfc-editor.org/rfc/rfc9204.html#name-encoder-and-decoder-streams
#
class QPACKParser:
  def __init__(self, conn: 'HTTPConnectionQUIC'):
    self.conn = conn

  def end(self):
    return False


#
# QPACK encoder parser
#
class QPACKParserEncoder(QPACKParser):
  def recv(self, data: bytes):
    try:
      with self.conn.qpack_decoder_access:
        ids = self.conn.qpack_decoder.feed_encoder(data)
    except self.conn.EncoderStreamError:
      self.conn.connection_error(ERROR_CODE.QPACK_ENCODER_STREAM_ERROR)
      return False

    return True



#
# QPACK decoder parser
#
class QPACKParserDecoder(QPACKParser):
  def recv(self, data: bytes):
    try:
      with self.conn.qpack_encoder_access:
        self.conn.qpack_encoder.feed_decoder(data)
      return True

    except self.conn.DecoderStreamError:
      self.conn.connection_error(ERROR_CODE.QPACK_DECODER_STREAM_ERROR)
      return False








#
# RFC 9000 16.
#
class varlenint:
  #
  # encode integer as variable-length bytes sequence
  #
  @staticmethod
  def encode(value: int):
    if value < 64:
      return value.to_bytes(1, 'big')

    if value < 16384:
      return (0x4000 | value).to_bytes(2, 'big')
    
    if value < 1073741824:
      return (0x80000000 | value).to_bytes(4, 'big')

    if value < 4611686018427387904:
      return (0xC000000000000000 | value).to_bytes(8, 'big')

    raise ValueError("value " + str(value) + " is too big to encode as RFC9000 variable length integer")


  #
  # decode variable-length integer
  # integer may be encoded on: 1, 2, 4 or 8 bytes
  #  return tuple=(decoded value, end offset)
  #
  @staticmethod
  def decode(data: bytes, at: int) -> 'tuple[int, int] | tuple[None, None]':
    if at >= len(data):
      return None, None # out of space

    fb = data[at] # first byte
    mark = fb & 0xC0 # marker

    if mark == 0: # 1 byte: 0-63
      return fb, at + 1

    if mark == 0x40: # 2 bytes: 0-16383
      if at + 2 > len(data):
        return None, None # out of space
      return ((fb & 0x3F) << 8) | data[at + 1], at + 2

    if mark == 0x80: # 4 bytes: 0-1073741823
      if at + 4 > len(data):
        return None, None # out of space
      return ((fb & 0x3F) << 24) | (data[at + 1] << 16) | (data[at + 2] << 8) | data[at + 3], at + 4

    # if mark == 0xC0: # 8 bytes: 0-4611686018427387903
    if at + 8 > len(data):
      return None, None # out of space
    return ((fb & 0x3F) << 56) | int.from_bytes(data[at + 1 : at + 8], 'big'), at + 8

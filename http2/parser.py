from .etc import SETTINGS, ERROR_CODE, FRAME_TYPE, FLAG, SHUT

from typing import TYPE_CHECKING
if TYPE_CHECKING:
  from .stream import HTTP2Stream
  from .handler import ProtocolHandlerHTTP2



#
# parsing income HTTP/2 bytes
#
class HTTP2Parser:
  #
  # frames receiving states in connection
  #
  class STATE:
    HEADER = 0 # connection is waiting to complete frame header
    PAYLOAD = 1 # connection is receiving frame payload data
    PREFACE = 2 # wait for the client preface - this is kind of 'client hello message' (RFC 9113 3.4)



  def __init__(self, root: 'ProtocolHandlerHTTP2'):
    self.root = root

    # current frame:
    self.state = HTTP2Parser.STATE.PREFACE # current state
    self.cache = bytes()
    self.flen = 0 # unsigned int 24-bits, The length of the frame payload
    self.ftype = 0 # unsigned int 8-bits, frame type
    self.fflags = 0 # unsigned int 8-bits, frame flags
    self.fid = 0 # unsigned int 31-bits, Stream Identifier
    self.continuation: 'HTTP2Stream | None' = None # if not None parsing is in reciving following CONTINUATION frames sequence



  #
  # called by connection on incoming connection bytes
  #
  def recv(self, stream: bytes) -> bool:
    self.cache += stream

    while True:
      if self.state == HTTP2Parser.STATE.HEADER:
        if len(self.cache) < 9: # if frame header not collected yet:
          return True # need more data

        self.flen = int.from_bytes(self.cache[0:3], 'big')
        self.ftype = self.cache[3]
        self.fflags = self.cache[4]
        self.fid = int.from_bytes(self.cache[5:9], 'big') & 0x7FFFFFFF # ignore the most significant bit
        self.state = HTTP2Parser.STATE.PAYLOAD # enter reading frame payload state
        self.cache = self.cache[9:] # remove header from cache

        if self.flen > 16384: # defult MAX_FRAME_SIZE
          #
          # RFC 9113 4.2
          # "An endpoint MUST send an error code of FRAME_SIZE_ERROR if a frame exceeds the size defined in
          #  SETTINGS_MAX_FRAME_SIZE, exceeds any limit defined for the frame type, or is too small to
          #  contain mandatory frame data. A frame size error in a frame that could alter the state of
          #  the entire connection MUST be treated as a connection error"
          #
          return self.root.streams.connection_error(ERROR_CODE.FRAME_SIZE_ERROR)


      elif self.state == HTTP2Parser.STATE.PAYLOAD:
        if len(self.cache) < self.flen: # if frame payload not completed yet:
          return True # need more data

        if not self.handle_frame(self.ftype, self.fflags, self.fid, self.cache[:self.flen]):
          return False # no more recv()

        self.state = HTTP2Parser.STATE.HEADER
        self.cache = self.cache[self.flen:] # remove payload from cache


      else: # if state == STATE_CONN_PREFACE:
        if len(self.cache) < 24: # if preface not collected (RFC9113 3.4):
          return True # need more data

        if self.cache.startswith(b'PRI * HTTP/2.0\r\n\r\nSM\r\n\r\n'): # if preface is correct:
          self.state = HTTP2Parser.STATE.HEADER # switch to header state
          self.cache = self.cache[24:] # remove preface from cache

        else: # if preface is invalid
          #
          # RFC 9113 3.4
          # "Clients and servers MUST treat an invalid connection preface as a
          #  connection error (Section 5.4.1) of type PROTOCOL_ERROR.  A GOAWAY
          #  frame (Section 6.8) MAY be omitted in this case, since an invalid
          #  preface indicates that the peer is not using HTTP/2."
          #
          self.root.send.shutdown(SHUT.IMMEDIATELY) # so just close connection, without send GOAWAY
          self.root.streams.goaway_recv = ERROR_CODE.NOT_HTTP2 # pseudo error to disable GOAWAY response
          return False # no more recv()



  #
  # handle recived frame from the client
  #
  def handle_frame(self, frame_type: int, flags: int, id: int, payload: bytes) -> bool:
    if self.continuation is not None: # if reading CONTINUATION sequence:
      if frame_type != FRAME_TYPE.CONTINUATION:
        return self.root.streams.connection_error(ERROR_CODE.PROTOCOL_ERROR)

      return self.handle_frame_continuation(flags, id, payload)


    if frame_type == FRAME_TYPE.DATA:
      return self.handle_frame_data(flags, id, payload)

    if frame_type == FRAME_TYPE.WINDOW_UPDATE:
      return self.handle_frame_window_update(id, payload)

    if frame_type == FRAME_TYPE.HEADERS:
      return self.handle_frame_headers(flags, id, payload)    

    if frame_type == FRAME_TYPE.RST_STREAM:
      return self.handle_frame_rst_stream(id, payload)

    if frame_type == FRAME_TYPE.SETTINGS:
      return self.handle_frame_settings(flags, id, payload)

    if frame_type == FRAME_TYPE.PRIORITY:
      return True # ignore: RFC 9113 deprecated PRIORITY frame

    if frame_type == FRAME_TYPE.PING:
      return self.handle_frame_ping(flags, id, payload)

    if frame_type == FRAME_TYPE.GOAWAY:
      return self.handle_frame_goaway(id, payload)
    
    if frame_type == FRAME_TYPE.CONTINUATION:
      if self.root.streams.goaway_send == ERROR_CODE.NO_ERROR and id > self.root.streams.last_openid:
        return True # ignore streams after GOAWAY

      return self.root.streams.connection_error(ERROR_CODE.PROTOCOL_ERROR)

    if frame_type == FRAME_TYPE.PUSH_PROMISE:
      #
      # reciving forbidden PUSH_PROMISE frame:
      #
      # RFC 9113 8.4
      # "A client cannot push. Thus, servers MUST treat the receipt of a PUSH_PROMISE frame as
      #  a connection error (Section 5.4.1) of type PROTOCOL_ERROR. A server cannot set the
      #  SETTINGS_ENABLE_PUSH setting to a value other than 0 (see Section 6.5.2)."
      #
      return self.root.streams.connection_error(ERROR_CODE.PROTOCOL_ERROR)

    return True # RFC 9113 4.1 "Implementations MUST ignore and discard frames of unknown types."



  #
  # handle DATA frame
  #  stream frame type
  #
  def handle_frame_data(self, flags: int, id: int, payload: bytes) -> bool:
    if id == 0: # must be a stream frame:
      return self.root.streams.connection_error(ERROR_CODE.PROTOCOL_ERROR)

    if flags & FLAG.PADDED: # if first byte is a pad length
      if len(payload) == 0:
        #
        # RFC 9113 6.1
        # If the length of the padding is the length of the frame payload or greater, the recipient
        # MUST treat this as a connection error (Section 5.4.1) of type PROTOCOL_ERROR.
        #
        return self.root.streams.connection_error(ERROR_CODE.PROTOCOL_ERROR)

      data_len = len(payload) - (1 + payload[0])
      if data_len < 0:
        #
        # RFC 9113 6.1
        #  "If the length of the padding is the length of the frame payload or greater, the recipient
        #   MUST treat this as a connection error (Section 5.4.1) of type PROTOCOL_ERROR."
        #
        return self.root.streams.connection_error(ERROR_CODE.PROTOCOL_ERROR)

      data = payload[1:1 + data_len]

    else:
      data = payload

    return self.root.streams.handle_data(id, len(payload), data, (flags & FLAG.END_STREAM) != 0)



  #
  # handle HEADERS frame
  #  stream frame type
  #
  def handle_frame_headers(self, flags: int, id: int, payload: bytes) -> bool:
    if id == 0: # must be a stream frame:
      return self.root.streams.connection_error(ERROR_CODE.PROTOCOL_ERROR)

    fbf_beg = 0
    fbf_end = len(payload)

    if flags & FLAG.PADDED: # if first byte is a pad length
      if fbf_end == 0:
        return self.root.streams.connection_error(ERROR_CODE.PROTOCOL_ERROR)

      fbf_beg = 1
      fbf_end -= payload[0] # pad length
      if fbf_beg > fbf_end:
        return self.root.streams.connection_error(ERROR_CODE.PROTOCOL_ERROR)

    if flags & FLAG.PRIORITY: # if next 5 bytes is a priority - RFC 9113 deprecated prioritizing:
      if fbf_end - fbf_beg < 5:
        return self.root.streams.connection_error(ERROR_CODE.PROTOCOL_ERROR)
      fbf_beg += 5

    return self.root.streams.handle_headers(id, payload[fbf_beg : fbf_end], (flags & FLAG.END_HEADERS) != 0, (flags & FLAG.END_STREAM) != 0)



  #
  # handle CONTINUATION frame
  #  stream frame type
  #
  def handle_frame_continuation(self, flags: int, id: int, payload: bytes) -> bool:
    if id != self.continuation.id: # must be a continuation stream frame:
      return self.root.streams.connection_error(ERROR_CODE.PROTOCOL_ERROR)

    return self.root.streams.handle_continuation(self.continuation, payload, (flags & FLAG.END_HEADERS) != 0)



  #
  # handle RST_STREAM frame
  #  stream frame type
  #
  def handle_frame_rst_stream(self, id: int, payload: bytes) -> bool:
    if id == 0: # must be a stream frame:
      return self.root.streams.connection_error(ERROR_CODE.PROTOCOL_ERROR)

    if len(payload) != 4:
      #
      # RFC 9113 6.4
      # "A RST_STREAM frame with a length other than 4 octets MUST be treated as
      #  a connection error (Section 5.4.1) of type FRAME_SIZE_ERROR."
      #
      return self.root.streams.connection_error(ERROR_CODE.FRAME_SIZE_ERROR)

    return self.root.streams.handle_rst_stream(id, int.from_bytes(payload, 'big'))



  #
  # handle SETTINGS frame
  #  connection frame type
  #
  def handle_frame_settings(self, flags: int, id: int, payload: bytes) -> bool:
    if id != 0: # must be a connection frame:
      return self.root.streams.connection_error(ERROR_CODE.PROTOCOL_ERROR)

    if flags & FLAG.ACK: # if ACK flag
      if len(payload) != 0:
        #
        # RFC 9113 6.5
        # "When this bit is set, the frame payload of the SETTINGS frame MUST be empty. Receipt of a
        #  SETTINGS frame with the ACK flag set and a length field value other than 0 MUST be treated as
        #  a connection error (Section 5.4.1) of type FRAME_SIZE_ERROR."
        #
        return self.root.streams.connection_error(ERROR_CODE.FRAME_SIZE_ERROR)

    else: # client updated own settings here:
      if len(payload) % 6 != 0:
        #
        # RFC 9113 6.5
        # "A SETTINGS frame with a length other than a multiple of 6 octets MUST be treated as a connection
        #  error (Section 5.4.1) of type FRAME_SIZE_ERROR."
        #
        return self.root.streams.connection_error(ERROR_CODE.FRAME_SIZE_ERROR)

      # parse settings:
      client_settings: 'list[tuple[int, int]]' = []

      for i in range(0, len(payload), 6):
        identifier = int.from_bytes(payload[i:i + 2], 'big') # 16-bit uint
        value = int.from_bytes(payload[i + 2:i + 6], 'big') # 32-bit uint

        if identifier == SETTINGS.HEADER_TABLE_SIZE:
          client_settings.append((SETTINGS.HEADER_TABLE_SIZE, value))

        elif identifier == SETTINGS.INITIAL_WINDOW_SIZE:
          if value < 2147483648:
            client_settings.append((SETTINGS.INITIAL_WINDOW_SIZE, value))
          else:
            return self.root.streams.connection_error(ERROR_CODE.PROTOCOL_ERROR)

        elif identifier == SETTINGS.MAX_FRAME_SIZE:
          if value >= 16384 and value < 16777216:
            client_settings.append((SETTINGS.MAX_FRAME_SIZE, value))
          else:
            return self.root.streams.connection_error(ERROR_CODE.PROTOCOL_ERROR)

      self.root.streams.handle_settings(client_settings)

    return True



  #
  # handle PING frame
  #  connection frame type
  #
  def handle_frame_ping(self, flags: int, id: int, payload: bytes) -> bool:
    if id != 0: # must be a connection frame:
      return self.root.streams.connection_error(ERROR_CODE.PROTOCOL_ERROR)

    if len(payload) != 8:
      return self.root.streams.connection_error(ERROR_CODE.FRAME_SIZE_ERROR)

    if (flags & FLAG.ACK) == 0:
      self.root.send.ping(payload) # just resent payload

    return True



  #
  # handle GOAWAY frame
  #  connection frame type
  #  GOAWAY is last recivied frame
  #
  def handle_frame_goaway(self, id: int, payload: bytes) -> bool:
    if id != 0: # must be a connection frame:
      return self.root.streams.connection_error(ERROR_CODE.PROTOCOL_ERROR)
    elif len(payload) < 8: # must be 8 bytes 'echo'
      return self.root.streams.connection_error(ERROR_CODE.FRAME_SIZE_ERROR)
    else:
      return self.root.streams.handle_goaway(int.from_bytes(payload[4:8], 'big'))



  #
  # handle WINDOW_UPDATE frame
  #  connection and stream frame type
  #
  def handle_frame_window_update(self, id: int, payload: bytes) -> bool:
    if len(payload) != 4:
      return self.root.streams.connection_error(ERROR_CODE.FRAME_SIZE_ERROR)

    return self.root.streams.handle_window_update(id, int.from_bytes(payload, 'big') & 0x7FFFFFFF)

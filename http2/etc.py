
# server configuration
class CONF:
  INITIAL_WINDOW_SIZE = 67108864 # 64MB per stream


# connection settings: RFC 9113 6.5.2
class SETTINGS:
  HEADER_TABLE_SIZE = 1
  #ENABLE_PUSH = 2
  #MAX_CONCURRENT_STREAMS = 3
  INITIAL_WINDOW_SIZE = 4
  MAX_FRAME_SIZE = 5
  #MAX_HEADER_LIST_SIZE = 6


# error codes: RFC 9113 section 7
class ERROR_CODE:
  NOT_HTTP2 = -1 # pseudo error code
  NO_ERROR = 0
  PROTOCOL_ERROR = 1
  INTERNAL_ERROR = 2
  FLOW_CONTROL_ERROR = 3
  SETTINGS_TIMEOUT = 4
  STREAM_CLOSED = 5
  FRAME_SIZE_ERROR = 6
  REFUSED_STREAM = 7
  CANCEL = 8
  COMPRESSION_ERROR = 9
  CONNECT_ERROR = 10
  ENHANCE_YOUR_CALM = 11
  INADEQUATE_SECURITY = 12
  HTTP_1_1_REQUIRED = 13
  
  _s = {
    NO_ERROR: "NO_ERROR",
    PROTOCOL_ERROR: "PROTOCOL_ERROR",
    INTERNAL_ERROR: "INTERNAL_ERROR",
    FLOW_CONTROL_ERROR: "FLOW_CONTROL_ERROR",
    SETTINGS_TIMEOUT:"SETTINGS_TIMEOUT",
    STREAM_CLOSED: "STREAM_CLOSED",
    FRAME_SIZE_ERROR: "FRAME_SIZE_ERROR",
    REFUSED_STREAM: "PROTOCOL_ERROR",
    CANCEL: "CANCEL",
    COMPRESSION_ERROR: "COMPRESSION_ERROR",
    CONNECT_ERROR: "CONNECT_ERROR",
    ENHANCE_YOUR_CALM: "ENHANCE_YOUR_CALM",
    INADEQUATE_SECURITY: "INADEQUATE_SECURITY",
    HTTP_1_1_REQUIRED: "HTTP_1_1_REQUIRED"
  }
  
  @staticmethod
  def to_str(error_code: int) -> str:
    if error_code in ERROR_CODE._s:
      return ERROR_CODE._s[error_code]
    else:
      return str(error_code)


# frame types: RFC 9113 section 6
class FRAME_TYPE:
  DATA = 0
  HEADERS = 1
  PRIORITY = 2 # deprecated
  RST_STREAM = 3
  SETTINGS = 4
  PUSH_PROMISE = 5
  PING = 6
  GOAWAY = 7
  WINDOW_UPDATE = 8
  CONTINUATION = 9


# frame flags
class FLAG:
  END_STREAM = 1
  END_HEADERS = 4
  PADDED = 8
  PRIORITY = 32
  ACK = 1



# sending state of 'HTTP2Stream.sendstate':
class SEND_STATE:
  NONE = 0   # stream sending not started yet - stream isn't in the sending queue, represents stage: #1 or #2
  ACTIVE = 1 # stream sending is active - stream should be in the sending queue if not found 'sending thread' is sending this stream in that moment on the TCP connection, represents stage #3
  DONE = 2   # stream sending done succesfully, no further sending job for this stream [stream isn't in the sending queue], represents CLOSED state
  ABORT_EARLY = 3 # stream send was aborted before sending started [stream never was in the sending queue]
  ABORT_SENDING = 4 # stream send was aborted during sending process [stream was in the sending queue and some data may be sent to the client before abort]


# thread stop reasons:
class STOP:
  NONE = 0  # still running...
  SHUT = 1  # exit on shutdown() response
  DROP = 2  # connection dropped (socket send() failed)
  EXCPT = 3 # exception occured in 'sending thread'


# HTTP2SendThread.shutdown() arguments:
class SHUT:
  NONE = 0
  PROC_SEND = 1 # exit when all processing threads are done and sending queue becomes empty
  SEND = 2 # exit when sending queue becomes empty
  IMMEDIATELY = 3 # exit as soon as possible

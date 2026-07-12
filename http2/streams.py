#
# if you want to use HTTP/2 python 'hpack' module i required
#  https://python-hyper.org/projects/hpack/en/latest/
#
import hpack

import threading, time
from .etc import CONF, ERROR_CODE, STOP, SHUT
from .stream import HTTP2Stream, CONF_HALF_WINDOW_SIZE
from .send import HTTP2SendThread

from typing import TYPE_CHECKING
if TYPE_CHECKING:
  from .handler import ProtocolHandlerHTTP2



#
# list that contains streams in states:
#  - 'open' or 'half-closed (remote)', stored in 'self.streams'
#  - 'closed' stream identifiers by stream error (RST_STREAM occurence), stored in 'self.rst'
# accessed from both: 'recv thread' and 'sending thread'
#
class StreamsList:
  def __init__(self):
    self.access = threading.Lock() # access to 'self.streams', dict is accessed from receiving and sending threads (recv thread adds, send thread removes streams)
    self.streams: 'dict[int, HTTP2Stream]' = dict() # 'open' or 'half-closed (remote)' streams, stream id is the key, HTTP2Stream object is value
    self.rst: 'set[int]' = set() # streams 'closed' by sent STREAM_RST (closed by the this side) - further recived frames on that streams must be ignored
    self.initial_window_size = 65535 # client stream initial window size

  #
  # returns true if has at least 1 stream in "open" state
  #
  def has_open_state(self) -> bool:
    with self.access:
      for stream in self.streams.values():
        if stream.is_open_state():
          return True

    return False

  #
  # open new stream
  #  called from 'recv thread'
  #
  def open(self, parent: 'HTTP2StreamsHandler', id: int) -> 'HTTP2Stream | None':
    stream = HTTP2Stream(parent, id)

    with self.access:
      stream.cws = self.initial_window_size # init from recv thread, all later updates are made from sending thread
      self.streams[id] = stream

    return stream

  #
  # get 'open' or 'half-closed (remote)' stream by id
  #  if stream isn't found (closed state) returned bool tells does steam was closed by RST from server
  #
  def get(self, id: int) -> 'tuple[HTTP2Stream, bool]':
    with self.access:
      stream = self.streams.get(id, None)
      return (stream, False) if stream is not None else (None, id in self.rst)
  
  #
  # closes stream if was in 'open' or 'half-closed (remote)' state (returns True then)
  # does nothing if stream is in 'idle' or 'closed' state (returns False then)
  #  called from 'recv thread' and 'sending thread'
  #  rst must be True when stream was closed by RST frame from the server
  #
  def close(self, id: int, rst: bool) -> bool:
    with self.access:
      if self.streams.pop(id, None) is not None:
        if rst: # if closed by RST_STREAM frame
          self.rst.add(id)
        return True

      return False # stream isn't open/half-closed
  
  #
  # close all streams in OPEN state by RST
  #  return their id list
  #
  def close_all_open_state(self) -> 'list[int]':
    ids: list[int] = list()
    
    with self.access:
      for id, stream in self.streams.items(): # collect:
        if stream.is_open_state():
          ids.append(id)
      
      for id in ids: # remove:
        self.streams.pop(id)
        self.rst.add(id)
    
    return ids

  #
  # this is called when stream is in 'closed' state by END_STREAM and RST_STREAM wasn't send before
  #
  def make_rst(self, id: int) -> bool:
    with self.access:
      if id not in self.rst:
        self.rst.add(id)
        return True

      return False


  #
  # update stream's window size
  #  called from 'send thread'
  #
  def update_window_size(self, id: int, adjust: int):
    with self.access:
      stream = self.streams.get(id, None)
      if stream is not None:
        stream.cws += adjust

  #
  # update client window size, called when client changed initial window size
  #  called from 'send thread'
  #
  def update_initial_window_size(self, new_value: int):
    with self.access:
      adjust = new_value - self.initial_window_size

      if adjust != 0:
        self.initial_window_size = new_value

        for stream in self.streams.values():
          stream.cws += adjust # update in existed stream, this may ends with cws as negative value





#
# HTTP/2 streams manager
#
# Possible states paths from: RFC 9113 5.1
#  IDLE -> [recv headers] -> OPEN -> [recv data] -> HALF-CLOSED (REMOTE) -> [send headers+data] -> CLOSED
#                                    [recv rst] -> CLOSED
#                                    [send rst] -> CLOSED with posibility to ignore frames from the client
#
class HTTP2StreamsHandler:
  def __init__(self, root: 'ProtocolHandlerHTTP2'):
    self.root = root
    self.streams = StreamsList() # 'open/half-closed (remote)' streams list
    self.last_openid = 0 # last client opened stream-id, every new client opened stream must have id greater than before and be an odd number
    self.sws = CONF.INITIAL_WINDOW_SIZE # current server connection window size (updated from recv thread)

    self.hpackd = hpack.Decoder()
    self.hpackd.header_table_size = 4096 # default HTTP/2 value

    self.goaway_send: int = None # if not None GOAWAY error code send to the client
    self.goaway_recv: int = None # if not None GOAWAY error code received from the client
    self.goaway_shutdown = False # if true calls send.shutdown() if no more open stremas

    self.inproc_access = threading.Lock()
    self.inproc_count = 0 # number of stream in processing thread, incremented in 'recv thread', decremented in 'processing thread', quered in 'sending thread'



  #
  # handle incoming HEADERS frame - client opens new stream
  #  called in 'recv thread'
  #
  def handle_headers(self, id: int, fbf: bytes, end_headers: bool, end_stream: bool) -> bool:
    if self.last_openid >= id:
      #
      # RFC 9113 5.1.1
      # "The identifier of a newly established stream MUST be numerically greater than all streams that the initiating
      #  endpoint has opened or reserved. This governs streams that are opened using a HEADERS frame and streams that
      #  are reserved using PUSH_PROMISE. An endpoint that receives an unexpected stream identifier MUST respond with
      #  a connection error (Section 5.4.1) of type PROTOCOL_ERROR."
      #
      return self.connection_error(self.last_openid, ERROR_CODE.PROTOCOL_ERROR)

    if self.goaway_send == ERROR_CODE.NO_ERROR:
      return True # ignore streams after GOAWAY
    
    if self.goaway_recv == ERROR_CODE.NO_ERROR:
      return self.connection_error(ERROR_CODE.PROTOCOL_ERROR) # client try to open stream after sent GOAWAY

    stream = self.streams.open(self, id) # new creaed stream instance
    self.last_openid = id # update last opened stream id
    
    if end_stream:
      stream.data = bytes() # no body in this stream

    if end_headers: # if no CONTINUATION
      stream.fbf = fbf # field block fragment completed
      if end_stream:
        return stream.start_process_resposne() # enter stream in stage 2 (half closed remote)

    else: # if CONTINUATION expected as next frame:
      stream.fbf.append(fbf)
      self.root.parser.continuation = stream # set parsing in CONTINUATION mode

    return True
  
  
  
  #
  # handle incoming CONTINUATION frame
  #  this may be called only after handle_headers() or handle_continuation() call
  #  called in 'recv thread'
  #
  def handle_continuation(self, stream: HTTP2Stream, fbf: bytes, end_headers: bool):
    if fbf:
      stream.fbf.append(fbf)

    if end_headers: # if last CONTINUATION frame in sequence:
      self.root.parser.continuation = None
      stream.fbf = b''.join(stream.fbf)

      if isinstance(stream.data, bytes): # if no DATA frames in income stream:
        return stream.start_process_resposne() # enter stream in stage 2 (half closed remote)

    return True
  
  
  
  #
  # handle incoming DATA frame
  #  called in 'recv thread'
  #  DATA can be recived from the client only in the 'open' state (or half-closed local)
  #
  #  window_size is the flow control size, RFC 9113 6.1:
  #   "The entire DATA frame payload is included in flow control,
  #    including the Pad Length and Padding fields if present"
  #  window_size may be different than 'len(data)' if padding was applied
  #
  def handle_data(self, id: int, window_size: int, data: bytes, end_stream: bool) -> bool:
    if id > self.last_openid: # if idle state:
      if self.goaway_send == ERROR_CODE.NO_ERROR:
        return True # ignore streams after GOAWAY

      #
      # RFC 9113 5.1 -> "idle state"
      # "Receiving any frame other than HEADERS or PRIORITY on a stream in this state MUST be treated as a connection
      #  error (Section 5.4.1) of type PROTOCOL_ERROR."
      #
      return self.connection_error(ERROR_CODE.PROTOCOL_ERROR)

    # need to consume connection window size
    self.sws -= window_size
    if self.sws <= CONF_HALF_WINDOW_SIZE:
      self.root.send.window_update_send(0, CONF.INITIAL_WINDOW_SIZE - self.sws)
      self.sws = CONF.INITIAL_WINDOW_SIZE
    
    # find stream
    stream, rst = self.streams.get(id)
    
    #
    # RFC 9113 6.1
    # "If a DATA frame is received whose stream is not in the "open" or "half-closed (local)" state,
    #  the recipient MUST respond with a stream error (Section 5.4.2) of type STREAM_CLOSED."
    #
    if stream is not None:
      if stream.is_open_state(): # if open state:
        stream.consume_sws(window_size) # need to consume stream server window size

        if data:
          stream.data.append(data)

        if end_stream:
          stream.data = b''.join(stream.data)
          return stream.start_process_resposne() # enter stream in stage 2 (half closed remote)

      else: # if "half-closed (remote)" state:
        stream.abort()
        self.streams.close(id, True)
        self.root.send.rst_stream(id, ERROR_CODE.STREAM_CLOSED) # send RST to the client

    elif not rst: # if closed by END_STREAM:
      self.streams.make_rst(id) # mark id as RST sent
      self.root.send.rst_stream(id, ERROR_CODE.STREAM_CLOSED) # send RST to the client

    return True
  
  
  
  #
  # handle incoming RST_STREAM frame
  #  called in 'recv thread'
  #
  def handle_rst_stream(self, id: int, error_code: int) -> bool:
    if id > self.last_openid: # if idle state:
      if self.goaway_send == ERROR_CODE.NO_ERROR:
        return True # ignore streams after GOAWAY

      #
      # RFC 9113 5.1 -> "idle state"
      # "RST_STREAM frames MUST NOT be sent for a stream in the "idle" state. If a RST_STREAM
      #  frame identifying an idle stream is received, the recipient MUST treat this as a
      #  connection error (Section 5.4.1) of type PROTOCOL_ERROR."
      #
      return self.connection_error(ERROR_CODE.PROTOCOL_ERROR)

    # find stream
    stream, _ = self.streams.get(id)

    if stream is not None: # if open or half-closed (remote) state:
      if not stream.is_open_state():
        stream.abort()

      self.streams.close(id, False)

    return True



  #
  # handle incoming WINDOW_UPDATE frame
  #  called in 'recv thread'
  #
  def handle_window_update(self, id: int, window_size_increment: int) -> bool:
    if id == 0: # if handle connection WINDOW_UPDATE:
      if window_size_increment == 0:
        return self.connection_error(ERROR_CODE.PROTOCOL_ERROR)

      self.root.send.window_update_from_client(0, window_size_increment) # send info to the sender
      return True

    else: # handle stream WINDOW_UPDATE:
      if id > self.last_openid: # if idle state:
        return self.connection_error(ERROR_CODE.PROTOCOL_ERROR)

      stream, _ = self.streams.get(id)

      if stream is not None: # if open or half-closed (remote):
        if window_size_increment != 0:
          self.root.send.window_update_from_client(id, window_size_increment) # send info to the sender
          return True

        if not stream.is_open_state():
          stream.abort()

        self.streams.close(id, True)
        self.root.send.rst_stream(id, ERROR_CODE.PROTOCOL_ERROR)

    return True



  #
  # handle incoming SETTINGS frame
  #  called in 'recv thread'
  #
  def handle_settings(self, client_settings: 'list[tuple[int, int]]'):
    #for opt in client_settings:
    #  identifier = opt[0]
    #  value = opt[1]

    #  if identifier == SETTINGS.HEADER_TABLE_SIZE: # update decoder table size
    #    self.hpackd.header_table_size = value # update in decoder

    self.root.send.settings(client_settings) # process settings in the sender



  #
  # called every period of time if sending thread is running
  #
  def check(self) -> None:
    if self.goaway_shutdown and not self.streams.has_open_state():
      self.root.send.shutdown(SHUT.PROC_SEND) # stop sending thread when all is done
      self.goaway_shutdown = False

  #
  # server requested to shutdown connection
  #  called from 'recv thread'
  #
  def connection_close_request(self) -> None:
    self.goaway_send = ERROR_CODE.NO_ERROR
    self.root.send.goaway_noerror(self.last_openid) # send GOAWAY with NO_ERROR to the client

    if self.streams.has_open_state():
      self.goaway_shutdown = True # wait until no opened streams
    else:
      self.root.send.shutdown(SHUT.PROC_SEND) # stop sending thread when all is done

    return True

  #
  # called when client sent GOAWAY frame that must be last income frame
  #  called from 'recv thread'
  #
  def handle_goaway(self, error_code: int) -> bool:
    self.goaway_recv = error_code

    if error_code != ERROR_CODE.NO_ERROR: # if connection error on the client side:
      self.root.send.shutdown(SHUT.IMMEDIATELY) # client will close TCP connection on connetion error
      return False # no more income frames -> continue in connection_close()

    # if graceful shutdown:
    if self.streams.has_open_state():
      self.goaway_shutdown = True # wait until no opened streams
    else:
      self.root.send.shutdown(SHUT.PROC_SEND) # stop sending thread when all is done

    return True


  #
  # handle HTTP/2 'connection error'
  #  stops receiving frames flow from the client and requests to immediately stop sending thread
  #  called from 'recv thread'
  #
  def connection_error(self, error_code: int) -> bool:
    self.goaway_send = error_code # send GOAWAY with 'error_code' in connection_close()
    self.root.send.shutdown(SHUT.IMMEDIATELY) # shutdown sending thread after send current streams, don't wait for requests in processing
    return False # continue in self.connection_close()
  
  
  #
  # finalize connection - called when connection is closing, right before close the connection socket
  #  here receiving is no longer possible, sending may still work
  #  called from 'recv thread'
  #
  def connection_close(self, close_reason: int) -> None:
    self.root.send.shutdown(SHUT.PROC_SEND)
    self.root.send.join()

    if self.root.send.stop != STOP.DROP and (self.goaway_recv is None or self.goaway_recv == ERROR_CODE.NO_ERROR): # if TCP connection not dropped and client didn't responsed with connection error:
      if self.root.send.stop == STOP.EXCPT: # if expection in sending thread:
        self.root.conn.send(HTTP2SendThread.make_goaway(self.last_openid, ERROR_CODE.INTERNAL_ERROR)) # respond with internal error

      elif self.goaway_send is not None and self.goaway_send != ERROR_CODE.NO_ERROR: # if has connection error to send:
        self.root.conn.send(HTTP2SendThread.make_goaway(self.last_openid, self.goaway_send)) # send GOAWAY with error code

      elif self.goaway_recv == ERROR_CODE.NO_ERROR and self.goaway_send is None: # if gracefull exit initiated from client:
        self.root.conn.send(HTTP2SendThread.make_goaway(self.last_openid, ERROR_CODE.NO_ERROR))

    # setup connection log:
    if self.root.send.stop == STOP.DROP:
      self.root.conn.cls_args.append('SEND-ERROR="connection dropped"')
    elif self.root.send.stop == STOP.EXCPT:
      self.root.conn.cls_args.append('SEND-ERROR="exception"')
    
    streams_count = len(self.streams.streams)
    if streams_count > 0:
      self.root.conn.cls_args.append('SENDING-DROPPED="unresponsed-requests-count=%d"' % (streams_count, ))
    
    if self.goaway_send is not None:
      self.root.conn.cls_args.append('ERROR-CODE-SERVER=' + ERROR_CODE.to_str(self.goaway_send))
    if self.goaway_recv is not None:
      self.root.conn.cls_args.append('ERROR-CODE-CLIENT=' + ERROR_CODE.to_str(self.goaway_recv))

    if self.root.send.stop != STOP.SHUT: # if send stopped abnormally:
      # there may be pending processing threads
      while self.query_inproc() > 0:
        time.sleep(0.05) # 50 ms
  
  
  



  
  
  #
  # operate on 'number of stream in processing' counter
  #
  def inc_inproc(self) -> None:
    with self.inproc_access:
      self.inproc_count += 1
  
  def dec_inproc(self) -> None:
    with self.inproc_access:
      if self.inproc_count == 0:
        raise RuntimeError("Trying decrement inproc_count when count is 0") # something is wrong...
      self.inproc_count -= 1
  
  def query_inproc(self) -> int:
    with self.inproc_access:
      return self.inproc_count

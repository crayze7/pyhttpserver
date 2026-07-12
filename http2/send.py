#
# if you want to use HTTP/2 python 'hpack' module i required
#  https://python-hyper.org/projects/hpack/en/latest/
#
import hpack

import threading
from .etc import CONF, SETTINGS, ERROR_CODE, FRAME_TYPE, FLAG, STOP, SHUT
from .stream import HTTP2Stream

from typing import TYPE_CHECKING
if TYPE_CHECKING:
  from .handler import ProtocolHandlerHTTP2



#
# returns complete frame as 'bytes' by extending payload as bytes with frame header
#
def frame_from_bytes(type: int, payload: bytes = bytes(), streamid: int = 0, flags: int = 0) -> bytes:
  return b''.join((len(payload).to_bytes(3, 'big'), type.to_bytes(1, 'big'), flags.to_bytes(1, 'big'), streamid.to_bytes(4, 'big'), payload))

#
# returns complete frame as 'bytes' by extending payload as list of bytes with frame header
#
def frame_from_list(type: int, payload: 'list[bytes]', streamid: int = 0, flags: int = 0) -> bytes:
  length = 0
  for chunk in payload:
    length += len(chunk)
  
  return b''.join([ length.to_bytes(3, 'big'), type.to_bytes(1, 'big'), flags.to_bytes(1, 'big'), streamid.to_bytes(4, 'big') ] + payload)



#
# make SETTINGS frame with server settings
#
def make_frame_settings() -> bytes:
  return frame_from_list(FRAME_TYPE.SETTINGS, [
    int(SETTINGS.INITIAL_WINDOW_SIZE).to_bytes(2, 'big'), int(CONF.INITIAL_WINDOW_SIZE).to_bytes(4, 'big')
  ])



class INDEX: # queue index
  PING               = 0 # highest priority
  GOAWAY             = 1
  SETTINGS           = 2
  WINDOW_UPDATE_SEND = 3
  RST_STREAM         = 4
  WINDOW_UPDATE_INC  = 5
  HEADERS            = 6
  DATA               = 7 # lowest priority



#
# sending queue
#
class SendingQueue:
  def __init__(self):
    self.notempty = threading.Event() # event set on every insertion, used to wake up 'sending thread' if was idle
    self.access = threading.Lock() # access to below lists

    self.ping: 'list[bytes]' = list() # PING to send: payload
    self.goaway: 'int | None' = None # if not None GOAWAY with NOERROR should be sent to the client
    self.settings: 'list[list[tuple[int, int]]]' = list() # SETTINGS to process and confirm: list(tuple(option, value))
    self.window_update_send: 'list[tuple[int, int]]' = list() # WINDOW_UPDATE to send: tuple(stream-id, inc_size)
    self.rst_stream: 'list[tuple[int, int]]' = list() # RST_STREAM to send: tuple(stream-id, error_code)
    self.window_update_inc: 'list[tuple[int, int]]' = list() # WINDOW_UPDATE from the client: tuple(stream-id, inc_size)
    self.headers: 'list[HTTP2Stream]' = list() # HEADERS to send
    self.data: 'list[HTTP2Stream]' = list() # DATA to send
  
  
  #
  # called from 'sending thread'
  #
  def pop(self, cws: int):
    with self.access:
      if len(self.ping) != 0:
        return (INDEX.PING, self.ping.pop(0))
      
      if self.goaway is not None:
        last_openid = self.goaway
        self.goaway = None
        return (INDEX.GOAWAY, last_openid)

      if len(self.settings) != 0:
        return (INDEX.SETTINGS, self.settings.pop(0))

      if len(self.window_update_send) != 0:
        return (INDEX.WINDOW_UPDATE_SEND, self.window_update_send.pop(0))

      if len(self.rst_stream) != 0:
        return (INDEX.RST_STREAM, self.rst_stream.pop(0))

      if len(self.window_update_inc) != 0:
        return (INDEX.WINDOW_UPDATE_INC, self.window_update_inc.pop(0))
      
      if len(self.headers) != 0:
        return (INDEX.HEADERS, self.headers.pop(0))

      if cws != 0: # if connection has available window size to send DATA:
        for i, stream in enumerate(self.data):
          if stream.cws != 0: # if stream has available window size to send DATA:
            self.data.pop(i)
            return (INDEX.DATA, stream)

      self.notempty.clear()
      return None


  #
  # remove stream from the queue (stream may be in 'headers' or 'data')
  #
  def remove_stream(self, stream: 'HTTP2Stream'):
    with self.access:
      for i, s in enumerate(self.headers):
        if s == stream:
          self.headers.pop(i)
          return

      for i, s in enumerate(self.data):
        if s == stream:
          self.data.pop(i)
          return





#
# this class implements sending queue and its thread
#
class HTTP2SendThread:
  def __init__(self, root: 'ProtocolHandlerHTTP2'): #conn: 'HTTPConnectionTCP', streams: 'HTTP2StreamsHandler'):
    self.root = root
    self.queue = SendingQueue() # queue with jobs evaluated in 'sending thread' and fill from 'recv thread'

    self.stop = STOP.NONE
    self.shut = SHUT.NONE # thread shutdown request, use shutdown() to set, possible shutdown values: SHUT.*

    self.hpacke = hpack.Encoder()
    self.hpacke.header_table_size = 4096 # default HTTP/2 value

    self.cws = 65535 # current client connection window size (updated from sending thread)
    self.max_payload_size = 16384 # client max frame payload size (start with default value)

    self.thread = threading.Thread(target=self.http2_tcp_connection_sending_thread, daemon=False)
    self.thread.start()
  
  
  #
  # returns True if there is not error in 'sending thread', which mean sending is running if no shutdown() was called
  #  called from 'recv thread'
  #
  def is_running(self) -> bool:
    return self.stop == STOP.NONE
  
  #
  # shutdown sending thread request
  #  called from 'recv thread'
  #
  def shutdown(self, shut) -> None:
    if shut > self.shut:
      self.shut = shut
      self.queue.notempty.set() # may wake up 'sending thread'
  
  #
  # this should be called after shutdown() to wait until thread stops
  #  called from 'recv thread'
  #
  def join(self) -> None:
    self.thread.join()



  #
  # send ping response
  #  called from 'recv thread'
  #
  def ping(self, payload: bytes) -> None:
    with self.queue.access:
      self.queue.ping.append(payload)
    self.queue.notempty.set()

  #
  # send goaway with no error
  #
  def goaway_noerror(self, last_openid: int):
    with self.queue.access:
      self.queue.goaway = last_openid
    self.queue.notempty.set()

  #
  # send client settings response
  #  called from 'recv thread'
  #
  def settings(self, settings: 'list[tuple[int, int]]') -> None:
    with self.queue.access:
      self.queue.settings.append(settings)
    self.queue.notempty.set()
  
  #
  # send WINDOW_UPDATE to the client
  #
  def window_update_send(self, id: int, inc_size: int):
    with self.queue.access:
      self.queue.window_update_send.append((id, inc_size))
    self.queue.notempty.set()

  #
  # send RST_STREAM to the client
  #  called from 'recv thread' and 'sending thread'
  #
  def rst_stream(self, id: int, error_code: int) -> None:
    with self.queue.access:
      self.queue.rst_stream.append((id, error_code))
    self.queue.notempty.set()
  
  #
  # process WINDOW_UPDATE from the client
  #  called from 'recv thread'
  #
  def window_update_from_client(self, id: int, inc_size: int) ->  None:
    with self.queue.access:
      self.queue.window_update_inc.append((id, inc_size))
    self.queue.notempty.set()



  #
  # push HTTP2Stram first time to the sending queue
  #  called from processing thread in HTTP2Stream.sendstate_access lock
  #
  def start_stream(self, stream: 'HTTP2Stream'):
    with self.queue.access:
      self.queue.headers.append(stream)
    self.queue.notempty.set()

  #
  # push HTTP2Stream to send the DATA frames
  # called from 'sending thread' in HTTP2Stream.sendstate_access lock
  #
  def continue_stream(self, stream: 'HTTP2Stream'):
    with self.queue.access:
      self.queue.data.append(stream)

  #
  # remove HTTP2Stream from the sending queue
  # called from 'recv thread' in HTTP2Stream.sendstate_access lock
  #
  def abort_stream(self, stream: 'HTTP2Stream'):
    self.queue.remove_stream(stream)



  #
  # connection 'sending thread' implementation
  #
  def http2_tcp_connection_sending_thread(self) -> None:
    try:
      task = None
      
      # server must starts with sending OPTIONS frame:
      if not self.send(make_frame_settings()):
        return

      # upgrade connection server window size to CONF.INITIAL_WINDOW_SIZE, HTTP2StreamsHandler.sws is initialized with CONF.INITIAL_WINDOW_SIZE
      if not self.send_window_update(0, CONF.INITIAL_WINDOW_SIZE - 65535):
        return

      while True:
        if task is None:
          self.queue.notempty.wait()
        
        if self.shut >= SHUT.IMMEDIATELY: # if critical shutdown, quit even if queue is not empty
          self.stop = STOP.SHUT
          return
        
        task = self.queue.pop(self.cws) # query for something to send
        
        if task is not None: # if got some task
          index = task[0]
          target = task[1]

          if index == INDEX.DATA:
            if not self.send_stream_data(target):
              return

          elif index == INDEX.HEADERS:
            if not self.send_stream_headers(target):
              return
          
          elif index == INDEX.WINDOW_UPDATE_INC:
            id = target[0]
            adjust = target[1]

            if id == 0: # if adjust connection window size:
              self.cws += adjust
            else: # if adjust stream window size
              self.root.streams.streams.update_window_size(id, adjust)

          elif index == INDEX.RST_STREAM:
            if not self.send_rst_stream(target[0], target[1]):
              return
          
          elif index == INDEX.WINDOW_UPDATE_SEND:
            if not self.send_window_update(target[0], target[1]):
              return
          
          elif index == INDEX.SETTINGS:
            if not self.send_settings_ack(target):
              return
          
          elif index == INDEX.PING:
            if not self.send_ping_ack(target):
              return
            
          elif index == INDEX.GOAWAY:
            if not self.send(HTTP2SendThread.make_goaway(target, ERROR_CODE.NO_ERROR)):
              return
          
          else: # not reachable:
            raise TypeError("SendingQueue incorrect INDEX")
        
        else: # queue is empty:
          shut = self.shut
          if shut >= SHUT.SEND: # if must quit when queue is empty:
            self.stop = STOP.SHUT
            return
          
          elif shut == SHUT.PROC_SEND: # if must quit when queue is empty and processing is done
            if self.root.streams.query_inproc() == 0: # query for number of streams in processing
              self.stop = STOP.SHUT
              return

    except Exception as e:
      self.stop = STOP.EXCPT
      try:
        self.root.conn.server.report_exception(e, None)
      except Exception:
        pass



  #
  # send one or more frames on TCP connection as stream client response, possible variants:
  #  - one HEADERS frame and zero or more CONTINUATION frames in following sequence
  #  called from 'sending thread'
  #
  def send_stream_headers(self, stream: HTTP2Stream) -> bool:
    if not stream.active_resposne(): # if aborted while waiting in the queue:
      return True

    id = stream.id

    if stream.excpt: # if error while processing:
      self.root.streams.streams.close(id, True) # close stream
      self.rst_stream(id, ERROR_CODE.INTERNAL_ERROR) # send RST_STREAM to the client
      return True

    fbf = self.hpacke.encode(stream.respond.headers) # headers HPACK compression
    body = stream.respond.body
    end_stream = False if body else True

    if len(fbf) <= self.max_payload_size: # if 'fbf' can be sent in single frame:
      if not self.send_headers(id, fbf, True, end_stream):
        return False

    else: # if CONTINUATION frames must be used to send whole 'fbf':
      if not self.send_headers(id, fbf[:self.max_payload_size], False, end_stream):
        return False
  
      fbf_beg = self.max_payload_size
      while True:
        fbf_end = fbf_beg + self.max_payload_size

        if fbf_end < len(fbf): # if not last CONTINUATION frame
          if not self.send_continuation(id, fbf[fbf_beg:fbf_end], False):
            return False
          fbf_beg = fbf_end

        else: # if last CONTINUATION frame
          if not self.send_continuation(id, fbf[fbf_beg:], True):
            return False
          break

    if end_stream: # if stream sending is done:
      stream.end_response() # close stream
    else:  # if DATA frames must be send later:
      stream.continue_response() # push again in the queue to continue later

    return True


  #
  # send one DATA frame on TCP connection as stream client response:
  #  called from 'sending thread'
  #
  def send_stream_data(self, stream: HTTP2Stream) -> bool:
    if not stream.active_resposne(): # if aborted while waiting in the queue:
      return True

    id = stream.id
    body = stream.respond.body

    sent = stream.sent # number of response body bytes already sent in response or None if it is first stream sending task
    sz = min(len(body) - sent, self.max_payload_size, self.cws, stream.cws) # number of bytes that is ready to send now
    end = sent + sz
    end_stream = end == len(body)

    if not self.send_data(id, body[sent : end], end_stream):
      return False

    self.cws -= sz
    stream.cws -= sz
    stream.sent += sz

    if end_stream: # if stream sending is done:
      stream.end_response() # close stream
    else:  # if DATA frames must be send later:
      stream.continue_response() # push again in the queue to continue later

    return True



  #
  # send DATA frame
  #  called from 'sending thread'
  #
  def send_data(self, id: int, data: bytes, end_stream: bool) -> bool:
    return self.send(frame_from_bytes(FRAME_TYPE.DATA, data, id, FLAG.END_STREAM if end_stream else 0))
  
  #
  # send HEADERS frame
  #  called from 'sending thread'
  #
  def send_headers(self, id: int, fbf: bytes, end_headers: bool, end_stream: bool) -> bool:
    return self.send(frame_from_bytes(FRAME_TYPE.HEADERS, fbf, id, (FLAG.END_HEADERS if end_headers else 0) | (FLAG.END_STREAM if end_stream else 0)))
  
  #
  # send CONTINUATION frame
  #  called from 'sending thread'
  #
  def send_continuation(self, id: int, fbf: bytes, end_headers: bool) -> bool:
    return self.send(frame_from_bytes(FRAME_TYPE.CONTINUATION, fbf, id, FLAG.END_HEADERS if end_headers else 0))
  
  #
  # send RST_STREAM frame
  #  called from 'sending thread'
  #
  def send_rst_stream(self, id: int, error_code: int) -> bool:
    return self.send(frame_from_bytes(FRAME_TYPE.RST_STREAM, error_code.to_bytes(4, 'big'), id))
  
  #
  # send SETTINGS ACK frame
  #  called from 'sending thread'
  #
  def send_settings_ack(self, settings: 'list[tuple[int, int]]') -> bool:
    for opt in settings:
      identifier = opt[0]
      value = opt[1]

      if identifier == SETTINGS.HEADER_TABLE_SIZE: # update decoder table size
        self.hpacke.header_table_size = value

      elif identifier == SETTINGS.INITIAL_WINDOW_SIZE: # if client updated new stream initial window size:
        self.root.streams.streams.update_initial_window_size(value)

      elif identifier == SETTINGS.MAX_FRAME_SIZE: # if client updated its max payload frame size:
        self.max_payload_size = min(value, 16384) # set client limit only if not greater than default

    return self.send(frame_from_bytes(FRAME_TYPE.SETTINGS, flags = FLAG.ACK))
  
  #
  # send PING ACK frame
  #  called from 'sending thread'
  #
  def send_ping_ack(self, payload: bytes) -> bool:
    return self.send(frame_from_bytes(FRAME_TYPE.PING, payload, flags = FLAG.ACK))
  
  #
  # send WINDOW_UPDATE frame
  #  called from 'sending thread'
  #
  def send_window_update(self, id: int, inc_size: int) -> bool:
    return self.send(frame_from_bytes(FRAME_TYPE.WINDOW_UPDATE, inc_size.to_bytes(4, 'big'), id))
  
  
  
  #
  # send buffer on the TCP connection
  #  called from 'sending thread'
  #
  def send(self, tcp_data: bytes) -> bool:
    if self.root.conn.send(tcp_data):
      return True
    
    else:
      self.stop = STOP.DROP # connection dropped
      return False



  #
  # create GOAWAY frame
  #  that frame can be send as last fram only from 'recv thread' when sending thread stopped
  #
  @staticmethod
  def make_goaway(last_stream_id: int, error_code: int) -> bytes:
    return frame_from_list(FRAME_TYPE.GOAWAY, [ last_stream_id.to_bytes(4, 'big'), error_code.to_bytes(4, 'big') ])

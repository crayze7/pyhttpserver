import time, threading
from base64 import b64encode
from hashlib import sha1
from .socket_ext import socket_tcp_set_keepalive
from .interface import HTTPInterfaceWebSocket
from .protocol import ProtocolBase
from .connection import HTTPConnection

from typing import TYPE_CHECKING
if TYPE_CHECKING:
  from .connection import HTTPConnectionTCP


#
# WebSocket 13 protocol
#  https://en.wikipedia.org/wiki/WebSocket
#  https://datatracker.ietf.org/doc/html/rfc6455
#
#   receiving messages is made on TCP connection thread where protcol was updated,
#   for sending extra thread in WebSocketSending is started
#   to close WebSocket connection with (it sends Go Away status code to the other side)
#
# UPDATE: it looks like current major browser implementations don't care about other messages than opcodes 1 and 2 (data messages)
#  https://stackoverflow.com/questions/10585355/sending-websocket-ping-pong-frame-from-browser
#
#  ping-pong opcodes are not used in Chrome and Firefox and are replaced by sending TCP SO_KEEPALIVE (chrome 45 seconds, firefox has 10 seconds intervals)
#   https://issues.chromium.org/issues/41309915
#   anyway this implementation supports pong response on income ping + sending ping, but since browser may ignore pings its better to not use it
#   so this implementation also sets SO_KEEPALIVE on socket to 10 seconds as disconnection detection
#
class WebSocket(ProtocolBase):
  #
  # compute 'Sec-WebSocket-Accept' header value
  #
  @staticmethod
  def compute_accept(key: str):
    return b64encode(sha1(key.encode("ascii") + b"258EAFA5-E914-47DA-95CA-C5AB0DC85B11").digest()).decode("ascii")


  def __init__(self):
    super().__init__()
    self.conn: 'HTTPConnectionTCP' = None
    self.receiving: 'WebSocketReceiving' = None
    self.sending: 'WebSocketSending' = None
    self.close_data: 'tuple[int, str | bytes | None]' = (WebSocketFrame.STATUS_CODE.GO_AWAY, b"BACKEND_SHUTDOWN") # used when sending close frame to initialize closing

  #
  # is TCP connection alive in both directions
  #
  def active(self):
    return self.conn is not None and self.sending is not None and self.sending.active()

  #
  # stops WebSocket connection if alive
  # it will send 'Go Away' status to the other side
  #
  def stop(self, status_code: int, reason: 'str | bytes | None' = None):
    self.close_data = (status_code, reason)
    self.conn.stop()

  #
  # send web socket message
  # this must be called from outside (higher leyer) for sending
  #
  def send(self, message: 'str | bytes'):
    return self.sending.send(message)

  #
  # called on income message - should be reimplemented in higher layer
  #  'message' type is dependent on message type
  #
  def onincome_message(self, message: 'str | bytes') -> None:
    pass

  #
  # send web socket ping
  #  payload max size is 125 bytes
  # return False means sending stopped
  #
  def send_ping(self, payload: bytes) -> bool:
    return self.sending.send_ping_frame(payload)

  #
  # called on income ping response - should be reimplemented in higher layer if send_ping is used
  #
  def onincome_pong(self, payload: bytes) -> None:
    pass


  #
  # assigning with TCP connection, called by TCP connection
  #  can be called only by ProtocolBase internally
  #
  def use_connection(self, conn: 'HTTPConnectionTCP'):
    conn.interface = HTTPInterfaceWebSocket(conn.interface) # upgrade protocol in interface

    # now connection becomes presistent "long lived", so need to enable TCP keep-alive:
    socket_tcp_set_keepalive(conn.socket, 10, 3, 3)

    self.conn = conn
    self.receiving = WebSocketReceiving(self)
    self.sending = WebSocketSending(self)

    conn.server.report_event('TCP.HTTP->WebSocket', conn.thread_id, [ conn.interface, conn.get_addr() ]) # report Upgrade on TCP connection

  #
  # called on income bytes from TCP connection
  #
  def recv(self, stream: bytes):
    return self.receiving.recv(stream) # pass

  #
  # called every ~100ms (until connection is reciving)
  #
  def check(self):
    return self.sending.fine() #  check does sending thread is "fine" - if not: abort also tcp recv thread

  #
  # called when server or some other instance want to close this connection
  #  calling self.stop()
  #
  def close_request(self):
    return self.sending.close_request()

  #
  # called when TCP connection(recv) is comming to close
  #
  def close(self, close_reason: int) -> None:
    if self.sending.active(): # if send close frame wasn't called - I think this may happen only in the case of exception:
      if close_reason == HTTPConnection.CLOSE_REASON.EXCEPT: # server expcetion in recv thread:
        self.sending.send_close_frame(WebSocketFrame.STATUS_CODE.INTERNAL_ERROR, None, False)
      else:
        self.sending.send_close_frame(self.close_data[0], self.close_data[1], False)

    self.sending.join() # sending thread should stop if is still alive

  #
  # reports event in server
  #
  def server_report_event(self, msg: str, *args):
    self.conn.server.report_event('WebSocket.' + msg, self.conn.thread_id, [ self.conn.get_addr(), *args ])






#
# receiving from web socket routine
#  implements parsing incoming protocol bytes
#
class WebSocketReceiving:
  def __init__(self, parent: 'WebSocket'):
    self.parent = parent
    self.frame: 'WebSocketFrameRecv | None' = None # current frame in receiving or None is "beetwen frames"
    self.msgtype = 0 # current message type: 0=none, 1=text message, 2=binarry message
    self.msgdata: 'list[bytes]' = list() # current message payload, may contains multiple bytes if fragmented in multiple frames
    self.cache = bytes() # data income from tcp socket to parse
    self.pos = 0


  def has(self, l: int):
    return len(self.cache) - self.pos >= l

  def fetch(self, l: int):
    r = self.cache[self.pos : self.pos + l]
    self.pos += l
    return r

  def done(self):
    self.cache = self.cache[self.pos:]
    self.pos = 0
    return True

  #
  # called by WebSocket on incoming protocol bytes (frames parsing)
  #
  def recv(self, stream: bytes) -> bool:
    self.cache += stream

    while True:
      if self.frame is None: # start reading frame:
        if self.has(2): # has first 2 bytes:
          head = self.fetch(2)
          self.frame = WebSocketFrameRecv(head[0], head[1])

          if self.frame.rsv != 0:
            self.parent.sending.send_close_frame(WebSocketFrame.STATUS_CODE.PROTOCOL_ERROR, b'unexpected extension bits', False) # close by protocol error: unexpected extension bits
            return False # fail
        else:
          return self.done() # need more data

      elif self.frame.state == WebSocketFrameRecv.STATE_PAYLOAD: # reading payload:
        if self.has(self.frame.payload_size):
          if not self.process_frame(self.fetch(self.frame.payload_size)):
            return False # fail

          self.frame = None
        else:
          return self.done() # need more data

      elif self.frame.state == WebSocketFrameRecv.STATE_PSIZE64: # reading 64-bit payload size:
        if self.has(8):
          self.frame.payload_size = int.from_bytes(self.fetch(8), byteorder='big', signed=False)
          self.frame.state = WebSocketFrameRecv.STATE_MASKING_KEY if self.frame.masked else WebSocketFrameRecv.STATE_PAYLOAD
        else:
          return self.done() # need more data

      elif self.frame.state == WebSocketFrameRecv.STATE_PSIZE16: # reading 16-bit payload size:
        if self.has(2):
          self.frame.payload_size = int.from_bytes(self.fetch(2), byteorder='big', signed=False)
          self.frame.state = WebSocketFrameRecv.STATE_MASKING_KEY if self.frame.masked else WebSocketFrameRecv.STATE_PAYLOAD
        else:
          return self.done() # need more data

      else: # elif self.frame.state == WebSocketFrameRecv.STATE_MASKING_KEY: # reading masking key:
        if self.has(4):
          self.frame.masking_key = self.fetch(4) # 4 bytes for masking key
          self.frame.state = WebSocketFrameRecv.STATE_PAYLOAD
        else:
          return self.done() # need more data


  #
  # processing incoming frame and its payload
  #
  def process_frame(self, payload: bytes):
    if len(payload) != 0 and self.frame.masked: # if need unmask:
      payload = WebSocketFrameRecv.unmask_payload(payload, self.frame.masking_key)

    if self.frame.opcode == 0: # a continuation frame:
      if self.msgtype == 0: # must be text or binarry here
        self.parent.sending.send_close_frame(WebSocketFrame.STATUS_CODE.PROTOCOL_ERROR, b'unexpected continuation frame', False) # close by protocol error: continuation frame without first frame
        return False # stop TCP recv thread

    elif self.frame.opcode == 1 or self.frame.opcode == 2: # a first frame:
      if self.msgtype == 0:
        self.msgtype = self.frame.opcode # corrent init first frame
      elif self.msgtype != self.frame.opcode:
        self.parent.sending.send_close_frame(WebSocketFrame.STATUS_CODE.PROTOCOL_ERROR, b'unexpected frame type', False) # close by protocol error: unexpected frame type
        return False # stop TCP recv thread

    elif self.frame.opcode == 8: # close frame:
      if self.parent.sending.is_alive(): # if this is True client is "close initiator" -> which mean backend must response also with close, if False this is response for close sended by backend before
        self.parent.sending.send_close_frame(payload, None, False) # connection closed by frontend
      return False # stop TCP recv thread

    elif self.frame.opcode == 9: # ping frame:
      self.parent.sending.send_pong_frame(payload) # must response with pong
      return True # continue

    elif self.frame.opcode == 10: # pong frame:
      try:
        self.parent.onincome_pong(payload) # process pong in higher layer
      except Exception as e:
        self.parent.conn.server.report_exception(e, None)
      return True

    else: # unknown frame:
      self.parent.sending.send_close_frame(WebSocketFrame.STATUS_CODE.PROTOCOL_ERROR, b'unknown frame type', False) # close by protocol error: unknown frame type
      return False # stop TCP recv thread

    self.msgdata.append(payload)

    if self.frame.fin: # if final frame:
      msgdata = b''.join(self.msgdata) # join fragments
      if self.msgtype == 1: # if utf-8 encoded message:
        try:
          msgdata = msgdata.decode()
        except UnicodeDecodeError:
          msgdata = None

      self.msgtype = 0
      self.msgdata.clear()

      if msgdata is not None:
        try:
          self.parent.onincome_message(msgdata) # process income message in higher layer
        except Exception as e:
          self.parent.conn.server.report_exception(e, None)
      else:
        self.parent.sending.send_close_frame(WebSocketFrame.STATUS_CODE.INVALID_PAYLOAD, 'invalid utf-8 in payload', False) # text message isn't valid utf-8
        return False # stop TCP recv thread

    return True





#
# sending in web socket routine
#
class WebSocketSending:
  CLOSE_RESPONSE_TIME = 2 # 2 seconds

  def __init__(self, parent: 'WebSocket'):
    self.parent = parent
    self.thread = threading.Thread(target=self.tcp_sending_thread, daemon=False)
    self.faccess = threading.Condition() # granting access to queues

    # queues with different priorities, new frames are appended at the end, sending thread pops from the begin
    self.fdata: 'list[bytes]' = list() # queue of data frames to send
    self.fping: 'list[bytes]' = list() # queue of ping frames to send
    self.fpong: 'list[bytes]' = list() # queue of pong frames to send

    self.fclose: 'None | bytes | True' = None # if None sending thread is operative, if not None, it's the last frame to send before exit thread, if tcp socket send() failed True is set here
    self.fcloselow: 'None | bytes' = None # same as fclose but with priority lower than others
    self.closetimeout: 'None | float' = None # time when waiting for client close response timeouts

    self.thread.start()


  def is_alive(self):
    return self.thread.is_alive()

  def join(self):
    self.thread.join()

  #
  # is sending still available (close not sent)
  #
  def active(self):
    with self.faccess:
      return self.fclose is None and self.fcloselow is None # until self.fclose is None sending thread should run

  #
  # called in recv() thread to check does sending thrad is "fine", if some problem is detected returns False which breaks also recv thread
  #  function alawyas returns True if thread is running
  #  otherwise function also may return True if close frame was sent and timeout for close response isn't come yet
  #
  def fine(self):
    with self.faccess:
      return self.is_alive() or (self.closetimeout is not None and time.monotonic() < self.closetimeout)

  #
  # request to close
  #
  def close_request(self):
    with self.faccess:
      if self.active(): # if sending available:
        self.send_close_frame(self.parent.close_data[0], self.parent.close_data[1], True)
        return True # continue reciving

      return self.closetimeout is not None and time.monotonic() < self.closetimeout # stop connection if timeouted


  #
  # send (when possible) web socket message to the other endpoint
  #  function only adds to the sending queue to send from sending thread when possible,
  #  so there is no possible failure in this method, but sending data to the socket itself may fail later
  #  return True -> message was queued to send, False -> sending was stopped
  #
  # NOTE: Chrome 134 make slices with payload max size equal to 131000 bytes. Lets do the same...
  #       Firefox 136 never makes slices and allways sends anything as single message, this is bad implementation... Trying to send 100MB ends with 'out of memory' in Firefox...
  #
  def send(self, message: 'str | bytes'):
    if isinstance(message, str):
      opcode = 1 # utf-8 text
      message = message.encode()
    else:
      opcode = 2 # binary

    if len(message) <= 131000: # if no more than 131000, no fragmentation need:
      return self.queue_push(self.fdata, WebSocketFrameSend(True, opcode, len(message)).encode(message))

    else: # if fragmentation is prefered, chunk size = 131000:
      frames = [WebSocketFrameSend(False, opcode, 131000).encode(message[:131000])] # first frame

      beg = 131000
      end = 262000
      while len(message) > end: # if not last frame:
        frames.append(WebSocketFrameSend(False, 0, 131000).encode(message[beg:end]))
        beg = end
        end += 131000

      frames.append(WebSocketFrameSend(True, 0, len(message) - beg).encode(message[beg:])) # last frame
      return self.queue_extend(self.fdata, frames)

  #
  # send ping frame
  #  max payload size is 125 bytes
  #
  def send_ping_frame(self, payload: bytes):
    return self.queue_push(self.fping, WebSocketFrameSend(True, 9, len(payload)).encode(payload))

  #
  # send pong frame
  #  max payload size is 125 bytes
  #
  def send_pong_frame(self, payload: bytes):
    return self.queue_push(self.fpong, WebSocketFrameSend(True, 10, len(payload)).encode(payload))

  #
  # send close frame
  #  sending that frame will stop sending thread from running
  #  flush -> True: try send all existed data (to sent) before close, False: close as fast as possible
  #
  def send_close_frame(self, status_code: 'int | bytes', reason: 'str | bytes| None', flush: bool):
    payload = int.to_bytes(status_code, 2, byteorder='big', signed=False) if isinstance(status_code, int) else status_code
    if isinstance(reason, str):
      reason = reason.encode(errors="ignore")
    if isinstance(reason, bytes):
      payload += reason

    frame = WebSocketFrameSend(True, 8, len(payload)).encode(payload)

    if flush:
      with self.faccess:
        if self.fclose is None and self.fcloselow is None:
          self.fcloselow = frame
          self.closetimeout = time.monotonic() + WebSocketSending.CLOSE_RESPONSE_TIME
          self.faccess.notify_all()
          return True

    else: # no flush - stop as fast as possible:
      with self.faccess:
        if self.fclose is None:
          self.fclose = frame
          self.closetimeout = time.monotonic() + WebSocketSending.CLOSE_RESPONSE_TIME
          self.faccess.notify_all()
          return True

    return False


  #
  # append into 1 of the queues
  #
  def queue_push(self, queue: list, frame: bytes):
    with self.faccess:
      if self.fclose or self.fcloselow:
        return False # sending more isn't available

      queue.append(frame)
      self.faccess.notify_all()
    return True

  #
  # extend 1 of the queues
  #
  def queue_extend(self, queue: list, frames: 'list[bytes]'):
    with self.faccess:
      if self.fclose or self.fcloselow:
        return False # sending more isn't available

      queue.extend(frames)
      self.faccess.notify_all()
    return True


  #
  # connection sending thread
  #
  def tcp_sending_thread(self) -> None:
    run = True
    while run:
      # get frame from the any queue:
      with self.faccess:
        while len(self.fdata) == 0 and len(self.fping) == 0 and len(self.fpong) == 0 and self.fclose is None and self.fcloselow is None: # if nothing to send:
          self.faccess.wait() # wait until something come

        if self.fclose is not None: # if has close frame to send:
          run = False # exit thread after send
          frame = self.fclose
        elif len(self.fpong) != 0: # if has PONG frame:
          frame = self.fpong.pop(0)
        elif len(self.fping) != 0: # if has PING frame:
          frame = self.fping.pop(0)
        elif len(self.fdata) != 0: # if has data frame:
          frame = self.fdata.pop(0)
        else: # if self.fcloselow is not None: # if has low priority close frame to send:
          run = False # exit thread after send
          frame = self.fcloselow
          self.fclose = frame

      if not self.parent.conn.send(frame): # send frame to the TCP socket
        # if sending frame failed:
        run = False # exit thread
        with self.faccess:
          self.fclose = True # mark socket failure
          self.closetimeout = None

    with self.faccess:
      self.fdata.clear()
      self.fping.clear()
      self.fpong.clear()





#
# web socket frame without payload (header only)
#
class WebSocketFrame:
  class STATUS_CODE:
    # 
    NORMAL = 1000 # Normal closure
    GO_AWAY = 1001 # Going away (e.g. browser tab closed)

    PROTOCOL_ERROR = 1002 # Protocol error
    INVALID_PAYLOAD = 1007 # Invalid payload data (e.g. non UTF-8 data in a text message)
    INTERNAL_ERROR = 1011 # Internal server error


  def __init__(self, fin: bool, opcode: int, payload_size: int):
    self.fin = fin # is final frame of the message
    self.opcode = opcode # 0, 1, 2, 8, 9, 10
    self.payload_size = payload_size



#
# web socket frame from Socket.recv()
#
class WebSocketFrameRecv(WebSocketFrame):
  STATE_PAYLOAD = 0 # reading payload
  STATE_MASKING_KEY = 1 # reading masking key
  STATE_PSIZE16 = 2 # reading 16-bit payload size
  STATE_PSIZE64 = 3 # reading 64-bit payload size

  def __init__(self, b0: int, b1: int):
    super().__init__((b0 & 0x80) != 0, b0 & 0xf, b1 & 0x7f)
    self.masked = (b1 & 0x80) != 0
    self.rsv = (b0 & 0x70) >> 4
    self.masking_key = None

    # init recv state:
    if self.payload_size == 126:
      self.state = WebSocketFrameRecv.STATE_PSIZE16
    elif self.payload_size == 127:
      self.state = WebSocketFrameRecv.STATE_PSIZE64
    elif self.masked:
      self.state = WebSocketFrameRecv.STATE_MASKING_KEY
    else:
      self.state = WebSocketFrameRecv.STATE_PAYLOAD

  #
  # unmasking payload
  #
  @staticmethod
  def unmask_payload(payload: bytes, mask: bytes):
    l = len(payload)
    m = len(mask)
    pa = bytearray(l)

    for i in range(l):
      pa[i] = payload[i] ^ mask[i % m]

    return bytes(pa)



#
# web socket frame for Socket.send()
#
class WebSocketFrameSend(WebSocketFrame):
  def encode(self, payload: bytes):
    f = [ int.to_bytes((0x80 if self.fin else 0) | self.opcode, 1, byteorder='big', signed=False) ]

    if self.payload_size > 65535: # more than 16-bit:
      f.append(int.to_bytes(127, 1, byteorder='big', signed=False))
      f.append(int.to_bytes(self.payload_size, 8, byteorder='big', signed=False))

    elif self.payload_size > 125: # more than 125 bytes:
      f.append(int.to_bytes(126, 1, byteorder='big', signed=False))
      f.append(int.to_bytes(self.payload_size, 2, byteorder='big', signed=False))

    else: # up to 125 bytes:
      f.append(int.to_bytes(self.payload_size, 1, byteorder='big', signed=False))

    f.append(payload)
    return b''.join(f)

#
# if you want to use HTTP/2 python 'hpack' module i required
#  https://python-hyper.org/projects/hpack/en/latest/
#
import hpack

import threading, time, email.utils
from .etc import CONF, ERROR_CODE, SEND_STATE
from ..utils import get_thread_id
from ..status import HTTPStatus
from ..request import HTTPRequest
from ..respond import HTTPRespond

from typing import TYPE_CHECKING
if TYPE_CHECKING:
  from .streams import HTTP2StreamsHandler
  from ..serv import HTTPServer


CONF_HALF_WINDOW_SIZE = int(CONF.INITIAL_WINDOW_SIZE / 2)



#
# HTTP/2 stream
#  if 'self' isn't present in 'self.parent.streams' it represents "half-closed (local)" state
#
class HTTP2Stream:
  def __init__(self, streams: 'HTTP2StreamsHandler', id: int):
    self.parent = streams # parent [readonly]
    self.id = id # stream id [readonly]

    # stage 1 - HTTP/2 open state [reading client request], object works only in 'recv thread':
    self.sws = CONF.INITIAL_WINDOW_SIZE # server's window size (how many bytes as DATA frames client is permited to send on this stream)
    self.fbf: 'list[bytes] | bytes | None' = list() # bytes if END_HEADERS was sent, None if not in open state
    self.data: 'list[bytes] | bytes | None' = list() # bytes if END_STREAM was sent, None if not in open state

    # stage 2 - HTTP/2 half-closed (remote) state [processing response in dedicated thread], object works in 'processing thread':
    self.request: 'HTTPRequest' = None # writing in 'recv thead', readonly in 'processing thread'
    self.respond: 'HTTPRespond' = None # writing in 'processing thread', readonly in 'sending thread'
    self.thread: threading.Thread = None # 'processing thread', writen in 'recv thread', right before 'processing thread' start
    self.excpt = False # True if exception occured in 'processing thread', may be writen in 'processing thread', right before thread exit

    # stage 3 - half-closed (remote) state [sending respond to the client in 'sending thread'], object works in 'sending thread':
    self.sendstate_access = threading.Lock() # lock used to access 'self.sendstate'
    self.sendstate = SEND_STATE.NONE # object becomes readable writeable in 'sending thread', in this state access from other thread must be synchronized by SendQueue.access lock
    self.cws = 0 # client's stream window size (how many bytes as DATA frames server is permited to send on this stream), set later in Lock, updated from sending thread
    self.sent = 0 # store currently sent stream body size,



  #
  # True if stream is in open state - client can send CONTINUATION or DATA frames with request
  #  object is accessed only in 'recv thread', other threads not works with this object in this stage yet
  #  in open state (stage 1):
  #   - 'fbf', 'data' are list or bytes
  #   - all other properties have initialized values and weren't writen yet
  #
  def is_open_state(self):
    return self.thread is None


  #
  # consume stream's server window size
  #  called only from 'recv thread' in open state when payload from DATA frame was consumed
  #
  def consume_sws(self, window_size: int):
    self.sws -= window_size
    if self.sws <= CONF_HALF_WINDOW_SIZE:
      self.parent.root.send.window_update_send(self.id, CONF.INITIAL_WINDOW_SIZE - self.sws)
      self.sws = CONF.INITIAL_WINDOW_SIZE


  #
  # starts stage #2: performs transition from 'open' to 'half closed (remote)' state
  #  called only from 'recv thread' in stage 1 to transform into stage 2
  #
  def start_process_resposne(self) -> bool:
    # create HTTP Request:
    self.request = HTTPRequest(self.parent.root.conn.interface, self.parent.root.conn.get_addr(), 'HTTP/2')
    self.request.body = self.data if len(self.data) != 0 else None
    
    # headers HPACK decompression:
    try:
      headers: 'list[tuple[str, str]]' = self.parent.hpackd.decode(self.fbf)
    except hpack.HPACKDecodingError:
      return self.parent.connection_error(ERROR_CODE.COMPRESSION_ERROR)
    
    # set request headers:
    for token, value in headers:
      name = token.lower()
      
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
    
    # check if request is malformed:
    if not self.request.method or not self.request.url:
      self.parent.streams.close(self.id, True) # remove from open strams list
      self.parent.root.send.rst_stream(self.id, ERROR_CODE.PROTOCOL_ERROR) # send RST to the client
      return True

    # enter stage #2:
    self.fbf = None
    self.data = None

    self.parent.inc_inproc() # increment number of streams in processing

    self.thread = threading.Thread(target=self.http2_request_processing_thread, daemon=False)
    self.thread.start()

    return True
  
  
  
  
  
  #
  # stream 'processing thread' implementation - implements job in stage #2
  #
  def http2_request_processing_thread(self) -> None:
    server = self.parent.root.conn.server
    respond = HTTPRespond()
    self.respond = respond

    try:
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
      
      # prepare HTTP/2 headers to encode:
      respond.headers = HTTP2Stream.make_respond_headers(server, respond, body_size, 2)
      
      if send_body_size == 0 and respond.body:
        respond.body = None # discard body
    
    
    except Exception as e:
      self.excpt = True
      try:
        server.report_exception(e, self.request)
      except Exception:
        pass
    
    finally:
      self.start_response()
  
  
  
  #
  # called from 'processing thread' to start sending process -> enter stage #3
  #
  def start_response(self) -> None:
    with self.sendstate_access:
      if self.sendstate == SEND_STATE.NONE: # stream must be in NONE sending state
        self.sendstate = SEND_STATE.ACTIVE # transition: NONE => ACTIVE
        self.parent.root.send.start_stream(self) # push into the sending queue

    self.parent.dec_inproc() # decrement number of streams in processing


  #
  # called before send frame to check does stream is still active, wasn't aborted while waiting in the queue
  # called from 'sending thread'
  #
  def active_resposne(self) -> bool:
    with self.sendstate_access:
      return self.sendstate == SEND_STATE.ACTIVE


  #
  # called when stream frame was sent to the client and further DATA frames will be send
  # called from 'sending thread'
  #
  def continue_response(self) -> None:
    with self.sendstate_access:
      if self.sendstate == SEND_STATE.ACTIVE: # if wasn't aborted
        self.parent.root.send.continue_stream(self) # push at the end to continue later


  #
  # called when whole stream response was sent
  # called from 'sending thread'
  #
  def end_response(self) -> None:
    close = False

    with self.sendstate_access:
      if self.sendstate == SEND_STATE.ACTIVE: # if wasn't aborted:
        self.sendstate = SEND_STATE.DONE
        close = True

    if close:
      self.parent.streams.close(self.id, False) # translate: "half-closed (remote)" -> "closed" by END_STREAM


  #
  # called when stram is closed by RST in "half-closed (remote)" state
  #  called from 'recv thread'
  #
  def abort(self) -> None:
    with self.sendstate_access:
      if self.sendstate == SEND_STATE.NONE: # if sending wasn't started
        self.sendstate = SEND_STATE.ABORT_EARLY # transition: NONE => ABORT_EARLY
        return # aborted early

      if self.sendstate == SEND_STATE.ACTIVE: # if sending is in progress
        self.sendstate = SEND_STATE.ABORT_SENDING # transition: ACTIVE => ABORT_SENDING
        self.parent.root.send.abort_stream(self)
        return # sending in progres, abort depends on 'abort_sending' value



  #
  # report start request in log
  # called from 'processing thread'
  #
  def log_report_start(self):
    reqline = '"%s %s"' % ( self.request.method, self.request.url ) # format HTTP1 like request pseudo line
    ev_args = [ self.parent.root.conn.get_addr(), reqline ]

    if self.request.getBodySize() > 0:
      ev_args.append('body=' + str(self.request.getBodySize()))

    self.parent.root.conn.server.report_event('HTTP2.REQ', get_thread_id(), ev_args)

  #
  # report start respponse in log
  # called from 'processing thread'
  #
  def log_report_response(self, duration: int, send_body_size: int):
    reqline = '"%s %s"' % ( self.request.method, self.request.url ) # format HTTP1 like request pseudo line
    ev_args = [ self.parent.root.conn.get_addr(), reqline, self.respond.status_code ]

    if send_body_size > 0:
      ev_args.append('body=' + str(send_body_size))

    ev_args.append('time=' + str(duration))

    self.parent.root.conn.server.report_event('HTTP2.RES', get_thread_id(), ev_args)



  #
  # create headers map: key -> value
  #
  @staticmethod
  def make_respond_headers(server: 'HTTPServer', respond: 'HTTPRespond', body_size: int, alt_svc_version: int):
    # prepare HTTP/2 headers to encode:
    headers = [
      (b':status', str(respond.status_code).encode('ascii')),
      (b'server', server.conf_server_header().encode('ascii')),
      (b'date', email.utils.formatdate(time.time(), usegmt=True).encode('ascii'))
    ]

    if respond.content_type:
      headers.append((b'content-type', respond.content_type.encode('ascii')))
    elif body_size > 0:
      headers.append((b'content-type', b'application/octet-stream')) # use default content type if not specified

    if respond.content_lang:
      headers.append((b'content-language', respond.content_lang.encode('ascii')))
    elif body_size > 0:
      default_content_lang = server.conf_default_content_lang()
      if default_content_lang:
        headers.append((b'content-language', default_content_lang.encode('ascii')))

    headers.append((b'content-length', str(body_size).encode('ascii')))

    # common headers:
    for key, value in respond.headers.items():
      key = key.lower()

      if key not in (
        'date', 'server', 'content-type', 'content-language', 'content-length', 'connection', 'keep-alive',
        'set-cookie', 'cache-control', 'pragma', 'expires', 'accept-ranges', 'upgrade', 'transfer-encoding', 'alt-svc'
      ):
        headers.append((key.encode('ascii'), value.encode('utf-8')))

    # cookies to set
    for set_cookie in respond.set_cookie:
      headers.append((b'set-cookie', set_cookie.encode('utf-8')))

    # setup cache:
    if respond.cache == 0: # if no cache
      headers.append((b'cache-control', b'no-cache, no-store, must-revalidate'))
    elif respond.cache > 0: # if cache for numer of seconds:
      headers.append((b'cache-control', b'max-age=' + str(respond.cache).encode('ascii')))
    else: # if cache for a long time (3 years):
      headers.append((b'cache-control', b'max-age=94670778'))

    if respond.accept_ranges is not None:
      headers.append((b'accept-ranges', respond.accept_ranges.encode('ascii')))

    alt_svc = server.alt_svc(alt_svc_version)
    if alt_svc:
      if isinstance(alt_svc, list):
        for svc in alt_svc:
          headers.append((b'alt-svc', svc.encode()))
      else:
        headers.append((b'alt-svc', alt_svc.encode()))

    return headers

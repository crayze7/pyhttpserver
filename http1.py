import time, datetime, email.utils
from .status import HTTPStatus
from .protocol import ProtocolBase
from .request import HTTPRequest
from .respond import HTTPRespond

from typing import TYPE_CHECKING
if TYPE_CHECKING:
  from .connection import HTTPConnectionTCP


#
# HTTP/1.x request protocol
#
class ProtocolHandlerHTTP1(ProtocolBase):
  def __init__(self):
    super().__init__()
    self.conn: 'HTTPConnectionTCP' = None
    self.cache = bytes()
    self.reset()

  def reset(self) -> None:
    self.request = None
    self.body_size = None
    self.send_buffer : list[bytes] = list()

  def use_connection(self, conn: 'HTTPConnectionTCP'):
    self.conn = conn

  def idle_timeout(self):
    return 30.0 # disconnect after 30 seconds inactivity


  def send(self, buffer: bytes) -> bool:
    if self.conn.send(buffer):
      return True
    else:
      self.conn.cls_error = True
      self.conn.cls_args.append('CONNECTION-DROPPED="sending failed: %s %s %s"' % (self.request.method, self.request.url, self.request.protocol))
      return False
  
  
  
  #
  # called by connection on incoming bytes
  #
  def recv(self, stream: bytes) -> 'bool | ProtocolBase':
    self.cache += stream

    while True:
      if self.body_size is None: # reading request headers:
        line = self.cache_getline()
        if isinstance(line, bool):
          return line # need more data or error
        
        if self.request is None:
          if not self.parse_start(line):
            return False # close connection
        
        elif len(line) > 0:
          if not self.parse_header_line(line):
            return False # close connection
        
        else: # if got empty line:
          if not self.parse_end():
            return False # close connection
      
      else: # reading request body until 'self.cache' got at least 'body_size' bytes
        if self.body_size > 0:
          if len(self.cache) > self.body_size:
            self.request.body = self.cache[:self.body_size]
            self.cache = self.cache[:self.body_size]
          
          elif len(self.cache) == self.body_size:
            self.request.body = self.cache
            self.cache = bytes()

          else:
            return True # need more buffer data

        result = self.process_request() # HTTP1 request is ready -> processing response

        if isinstance(result, ProtocolBase):
          return result # Switch Protocols

        if not result: # if False
          return False # close connection
  
  
  
  #
  # returns next line from 'buffer'
  #
  def cache_getline(self) -> 'bytes | bool':
    pos = self.cache.find(b'\r\n')
    
    if pos == -1: # if not found new line
      if len(self.cache) > 65536:
        if self.request is None:
          self.send_error(HTTPStatus.REQUEST_URI_TOO_LONG)
        else:
          self.send_error(HTTPStatus.REQUEST_HEADER_FIELDS_TOO_LARGE)
        return False # close connection
      else:
        return True # need more buffer data
    
    else: # if got new line:
      line = self.cache[:pos]
      self.cache = self.cache[pos + 2:]
      
      return line # return bytes() object
  
  
  
  #
  # parsing HTTP status line
  #
  def parse_start(self, line: bytes) -> bool:
    try:
      words = line.split(b' ')
      
      if len(words) == 3: # expects: ( METHOD, URI path , HTTP1/1.0|HTTP/1.1 )
        method = words[0].decode('ascii').upper()
        if method not in ('GET', 'HEAD', 'POST', 'PUT', 'DELETE', 'OPTIONS', 'PATCH'):
          self.send_error(HTTPStatus.METHOD_NOT_ALLOWED)
          return False # close connection
        
        path = words[1]
        
        version = words[2].decode('ascii').upper()
        if version not in ('HTTP/1.0', 'HTTP/1.1'):
          self.send_error(HTTPStatus.HTTP_VERSION_NOT_SUPPORTED)
          return False # close connection
      
      elif len(words) == 2: # expects HTTP/0.9 style: ( GET, URI path )
        method = words[0].decode('ascii').upper()
        if method != 'GET':
          self.send_error(HTTPStatus.METHOD_NOT_ALLOWED)
          return False # close connection
        
        path = words[1]
        version = 'HTTP/0.9'
      
      else:
        self.send_error(HTTPStatus.BAD_REQUEST)
        return False # close connection
      
      # validate URI path:
      if len(path) == 0:
        self.send_error(HTTPStatus.BAD_REQUEST)
        return False # close connection
      
      path = path.decode('iso-8859-1')
      for c in path:
        b = ord(c)
        if b < 33:
          self.send_error(HTTPStatus.BAD_REQUEST)
          return False # close connection
      
      self.request = HTTPRequest(self.conn.interface, self.conn.get_addr(), version, method, path) # start new request
      return True
    
    except UnicodeDecodeError:
      self.send_error(HTTPStatus.BAD_REQUEST)
      return False # close connection
  
  
  
  #
  # parsing HTTP request header line
  #
  def parse_header_line(self, line: bytes) -> bool:
    pos = line.find(b':')
    if pos > 0:
      try:
        token = line[:pos].decode('ascii')
        value = line[pos + 1:].strip(b' \t\v\r\n').decode('iso-8859-1')
      except UnicodeDecodeError:
        self.send_error(HTTPStatus.BAD_REQUEST)
        return False
      
      if token and value:
        tokenl = token.lower()

        if tokenl == 'host':
          self.request.host = value

        elif tokenl != 'cookie':
          self.request.headers.set(token, value)
        
        else: # process cookie header:
          for cookie in value.split(';'):
            pair = cookie.split('=')
            if len(pair) == 2:
              self.request.cookies[pair[0].lstrip()] = pair[1]
    
    else:
      self.send_error(HTTPStatus.BAD_REQUEST)
      return False
    
    return True
  
  
  
  #
  # end headers parsing when got empty line
  #
  def parse_end(self) -> bool:
    expect = self.request.getHeader('Expect')
    if expect and expect.lower() == '100-continue' and self.request.protocol == 'HTTP/1.1':
      self.send_response_only(HTTPStatus.CONTINUE)
      if self.send_flush():
        self.reset()
        return True
      else:
        return False
    
    self.body_size = 0
    
    content_length = self.request.getHeader('Content-Length')
    if content_length is not None and content_length.isdigit():
      content_length = int(content_length)
      if content_length > 0:
        self.body_size = content_length
    
    return True



  #
  # 'self.request' is ready to processing
  #
  def process_request(self) -> 'bool | ProtocolBase':
    req = self.request
    respond = HTTPRespond()

    ev_args = [ self.conn.get_addr(), '"%s %s %s"' % (req.method, req.url, req.protocol) ]
    if req.getBodySize() > 0:
      ev_args.append('body=' + str(req.getBodySize()))
    self.conn.server.report_event('HTTP1.REQ', self.conn.thread_id, ev_args)
    
    start_time = time.perf_counter()
    
    try:
      self.conn.server.process_request(req, respond) # processing the request
      respond.postprocess(req, self.conn.server.conf_body_encoding()) # postprocessing the respond
    
    except Exception as e:
      self.send_error(HTTPStatus.INTERNAL_SERVER_ERROR, time.perf_counter() - start_time)
      try:
        self.conn.server.report_exception(e, req)
      except Exception:
        pass
      return False

    body_size = len(respond.body) if respond.body else 0
    send_body_size = body_size if req.method != 'HEAD' else 0 # same as 'body_size' or 0 if 'HEAD' request

    self.send_response(respond.status_code, send_body_size, time.perf_counter() - start_time)
    
    if respond.content_type:
      self.send_header('Content-Type', respond.content_type)
    elif body_size > 0:
      self.send_header('Content-Type', 'application/octet-stream') # use default content type if not specified
    
    if respond.content_lang:
      self.send_header('Content-Language', respond.content_lang)
    elif body_size > 0:
      default_content_lang = self.conn.server.conf_default_content_lang()
      if default_content_lang:
        self.send_header('Content-Language', default_content_lang)
    
    self.send_header('Content-Length', str(body_size))

    # connection:
    if respond.status_code != 101: # if not Switch Protocols:
      header_conn = req.getHeader('Connection', '').lower()

      if header_conn: # if 'Connection' included in request
        keep_alive = header_conn == 'keep-alive' # client must set 'keep-alive'
      else: # no 'Connection' in request, use default behaviour
        keep_alive = req.protocol == 'HTTP/1.1' # for HTTP/1.1 keep connection alive is default

      if keep_alive:
        self.send_header('Connection', 'keep-alive')
      else:
        self.send_header('Connection', 'close')
    else:
      self.send_header('Connection', 'upgrade')

    # common headers:
    for key, value in respond.headers.items():
      if key.lower() not in (
        'date', 'server', 'content-type', 'content-language', 'content-length', 'connection', 'keep-alive',
        'set-cookie', 'cache-control', 'pragma', 'expires', 'accept-ranges'
      ):
        self.send_header(key, value)
    
    # cookies to set
    for set_cookie in respond.set_cookie:
      self.send_header('Set-Cookie', set_cookie)
    
    if respond.status_code != 101:
      # setup cache:
      if respond.cache == 0: # if no cache
        self.send_header('Cache-Control', 'no-cache, no-store, must-revalidate')
        self.send_header('Pragma', 'no-cache')
        self.send_header('Expires', 'Thu, 01 Jan 1970 00:00:00 GMT')

      elif respond.cache > 0: # if cache for numer of seconds:
        self.send_header('Cache-Control', 'max-age=' + str(respond.cache))
        self.send_header('Expires', datetime.datetime.fromtimestamp(int(time.time()) + respond.cache).strftime('%a, %d %b %Y %H:%M:%S GMT'))

      else: # if cache for a long time (3 years):
        self.send_header('Cache-Control', 'max-age=94670778')
        self.send_header('Expires', datetime.datetime.fromtimestamp(int(time.time()) + 94670778).strftime('%a, %d %b %Y %H:%M:%S GMT'))

      if respond.accept_ranges is not None:
        self.send_header('Accept-Ranges', respond.accept_ranges)

    if not self.send_flush():
      return False

    if send_body_size > 0 and not self.send(respond.body):
      return False

    self.reset()
    return keep_alive if respond.status_code != 101 or respond.switch_protocols is None else respond.switch_protocols



  #
  # responds with error and reset request
  #
  def send_error(self, code: int, duration: 'float | None' = None) -> None:
    self.send_response(code, duration=duration)
    self.send_header('Cache-Control', 'no-cache, no-store, must-revalidate')
    self.send_header('Pragma', 'no-cache')
    self.send_header('Expires', 'Thu, 01 Jan 1970 00:00:00 GMT')
    self.send_header('Connection', 'close')
    
    if self.send_flush():
      self.reset()
  
  
  
  #
  # starts sending response to client
  #
  def send_response(self, code: int, body_size: int = 0, duration: 'float | None' = None) -> None:
    alt_svc = self.conn.server.alt_svc(1)
    arg_reqline = '"%s %s %s"' % ( self.request.method, self.request.url, self.request.protocol ) if self.request is not None else '"- - -"'
    ev_args = [ self.conn.get_addr(), arg_reqline, code ]
    
    if body_size > 0:
      ev_args.append('body=' + str(body_size))
    if duration is not None:
      ev_args.append('time=' + str(duration))
    
    self.conn.server.report_event('HTTP1.RES', self.conn.thread_id, ev_args)
    
    self.send_response_only(code)
    self.send_header('Server', self.conn.server.conf_server_header())
    self.send_header('Date', email.utils.formatdate(time.time(), usegmt=True))

    if alt_svc:
      if isinstance(alt_svc, list):
        for svc in alt_svc:
          self.send_header('Alt-Svc', svc)
      else:
        self.send_header('Alt-Svc', alt_svc)
  
  
  def send_response_only(self, code: int) -> None:
    version = self.request.protocol if self.request is not None else 'HTTP/1.1'
    if version != 'HTTP/0.9':
      message = HTTPStatus.str(code)
      if not message:
        message = 'Unknown'
      
      self.send_buffer.append(("%s %d %s\r\n" % (version, code, message)).encode('iso-8859-1', errors='strict'))
  
  
  def send_header(self, keyword: str, value: str) -> None:
    if self.request is None or self.request.protocol != 'HTTP/0.9':
      self.send_buffer.append(("%s: %s\r\n" % (keyword, value)).encode('iso-8859-1', errors='strict'))
  
  
  def send_flush(self) -> bool:
    if self.request is None or self.request.protocol != 'HTTP/0.9':
      self.send_buffer.append(b'\r\n')
      buffer = b''.join(self.send_buffer)
      self.send_buffer = list()
      return self.send(buffer)
    
    else:
      return True



  def close(self, close_reason: int) -> None:
    from .connection import HTTPConnectionTCP

    if close_reason == HTTPConnectionTCP.CLOSE_REASON.CLIENT:
      if self.request is not None:
        self.conn.cls_error = True
        self.conn.cls_args.append('CONNECTION-CLOSED="receiving failed: %s %s %s"' % (self.request.method, self.request.url, self.request.protocol))

    elif close_reason == HTTPConnectionTCP.CLOSE_REASON.ABORT:
      if self.request is not None:
        self.conn.cls_error = True
        self.conn.cls_args.append('CONNECTION-ABORTED="receiving failed: %s %s %s"' % (self.request.method, self.request.url, self.request.protocol))


  def switchby(self, _: 'ProtocolBase'):
    self.conn = None # unref TCP connection

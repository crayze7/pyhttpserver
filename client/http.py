import ssl
from http.client import HTTPConnection, HTTPSConnection, RemoteDisconnected, IncompleteRead, CannotSendRequest, CannotSendHeader



#
# making http or https requests base class
#
class BaseHTTPReq:
  def __init__(self, hostname: str, user_agent: str, connect_timeout: 'float | None', port: 'int | None'):
    self.hostname = hostname
    self.port = port
    self.user_agent = user_agent
    self.connect_timeout = connect_timeout
    self.conn: HTTPConnection | HTTPSConnection = None

  #
  # request result
  #
  class Result:
    def __init__(self, body = b'', content_type: str = None, status_code = 200):
      self.status_code = status_code
      self.body = body
      self.content_type = content_type

  def connect(self):
    pass


  #
  # do http request
  #
  def do_request(self, method: str, path: str, body: bytes = None, headers: 'dict[str, str]' = None, keep_alive=True, io_timeout: 'float | None' = None) -> 'BaseHTTPReq.Result':
    if self.conn is None or self.conn.sock is None:
      self.connect()
    
    heads = dict(headers) if headers is not None else dict()
    heads['Host'] = self.hostname
    heads['Connection'] = 'keep-alive' if keep_alive else 'close'
    heads['Content-Length'] = str(len(body)) if body is not None else '0'
    heads['Cache-Control'] = 'no-cache'
    heads['Pragma'] = 'no-cache'
    heads['User-Agent'] = self.user_agent

    reconnect_no = 0
    while True:
      try:
        self.conn.sock.settimeout(io_timeout) # set IO timeout
        self.conn.request(method, path, body, heads) # send request
        resp = self.conn.getresponse() # get response
        data = resp.read() # read body
        break # response is fine -> no reconnect

      except (RemoteDisconnected, IncompleteRead, CannotSendRequest, CannotSendHeader) as e:
        if reconnect_no < 3:
          reconnect_no += 1
          #util.print("BaseHTTPReq.do_request() failed:", str(e), "- reconnect-no:", reconnect_no)
          self.reconnect()
        else:
          raise e

      except (TimeoutError, OSError) as e:
        #util.print("BaseHTTPReq.do_request() timeouted")
        self.close()
        raise e

    result = BaseHTTPReq.Result(data, resp.getheader('Content-Type'), resp.status)
    if not keep_alive:
      self.close()
    
    return result


  #
  # close connection
  #
  def close(self):
    if self.conn is not None:
      try:
        self.conn.close()
      except OSError:
        pass # silent ignore
      self.conn = None


  #
  # reconnect
  #
  def reconnect(self):
    self.close()
    self.connect()





#
# making http requests
#
class HTTPReq(BaseHTTPReq):
  def __init__(self, hostname: str, user_agent: str, connect_timeout: 'float | None' = 5, port: 'int | None' = None):
    super().__init__(hostname, user_agent, connect_timeout, port)

  def connect(self):
    try:
      self.conn = HTTPConnection(self.hostname, self.port, timeout=self.connect_timeout)
      self.conn.connect()

    except (TimeoutError, OSError, ConnectionError):
      self.conn = None
      raise ConnectionError("Failed to connect: hostname=", self.hostname, ", port=", self.port)



#
# making https requests
#
class HTTPSReq(BaseHTTPReq):
  def __init__(self, hostname: str, user_agent: str, connect_timeout: 'float | None' = 5, port: 'int | None' = None):
    super().__init__(hostname, user_agent, connect_timeout, port)
    self.ssl_ctx = ssl.create_default_context()

  def connect(self):
    try:
      self.conn = HTTPSConnection(self.hostname, self.port, timeout=self.connect_timeout, context=self.ssl_ctx)
      self.conn.connect()

    except (TimeoutError, OSError, ConnectionError, ssl.SSLError):
      self.conn = None
      raise ConnectionError("Failed to connect: hostname=", self.hostname, ", port=", self.port)







#
# making http requests by url
#  object is not thread safe
#
class HTTPClient:
  @staticmethod
  def request(user_agent: str, method: str, url: str, body: bytes=None, headers={}, connect_timeout: 'float | None' = 5, io_timeout: 'float | None' = None) -> 'BaseHTTPReq.Result':
    req = HTTPClient(user_agent, connect_timeout)
    result = req.do_request(method, url, body, headers, False, io_timeout)
    req.close()
    return result


  @staticmethod
  def request_get(user_agent: str, url: str, headers: 'dict[str, str]' = {}, connect_timeout: 'float | None' = 5, io_timeout: 'float | None' = None):
    return HTTPClient.request(user_agent, "GET", url, None, headers, connect_timeout, io_timeout)


  def __init__(self, user_agent: str, connect_timeout: 'float | None' = 5):
    self.user_agent = user_agent
    self.connect_timeout = connect_timeout
    self.conns: dict[str, HTTPReq | HTTPSReq] = dict() # http and https connections dict

  def close(self):
    for conn in self.conns.values():
      conn.close()

    self.conns.clear()



  def do_request(self, method: str, url: str, body: bytes=None, headers={}, keep_alive = True, io_timeout: 'float | None' = None) -> 'BaseHTTPReq.Result':
    if url.startswith("http://"):
      scheme = "http://"
      pos = url.find('/', 7)
      if pos != -1:
        hostname = url[7:pos]
        path = url[pos:]
      else:
        hostname = url[7:]
        path = '/'
    
    elif url.startswith("https://"):
      scheme = "https://"
      pos = url.find('/', 8)
      if pos != -1:
        hostname = url[8:pos]
        path = url[pos:]
      else:
        hostname = url[8:]
        path = '/'
    
    else:
      raise RuntimeError("invalid HTTPS protocol in url: " + str(url))
    
    pos = hostname.find(':')
    if pos == -1:
      port = None
    else: # if contains port:
      port = int(hostname[pos + 1:])
      hostname = hostname[:pos]

    
    key = scheme + hostname
    if key in self.conns:
      conn = self.conns[key] # use existed connection
    else:
      if scheme == "https://":
        conn = HTTPSReq(hostname, self.user_agent, self.connect_timeout, port)
      else:
        conn = HTTPReq(hostname, self.user_agent, self.connect_timeout, port)
      self.conns[key] = conn
    
    return conn.do_request(method, path, body, headers, keep_alive, io_timeout)

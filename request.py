from .interface import HTTPInterface


#
# HTTP request data
#
class HTTPRequest:
  #
  # HTTP headers collection
  #  header names are case insensitive
  #
  class Headers:
    def __init__(self):
      self.h = dict()
    
    def set(self, token: str, value: str) -> None:
      self.h[token.lower()] = value
    
    def get(self, token: str, default=None) -> 'str | None':
      token = token.lower()
      
      if token in self.h:
        return self.h[token]
      else:
        return default
    
    def items(self):
      return self.h.items()
  
  
  
  def __init__(self, interface: HTTPInterface, addr: str, protocol: str, method: 'str | None' = None, url: 'str | None' = None):
    self.interface = interface # listening interface (HTTPInterface object)
    self.addr = addr # client connection address (str)
  
    self.protocol = protocol # 'HTTP/0.9' or 'HTTP/1.0' or 'HTTP/1.1' or 'HTTP/2' (str) or WebSocket
    self.method = method # HTTP request method: GET, HEAD, POST, ... (str)
    self.host: 'str | None' = None # HTTP/1 Host or HTTP/2 :authority header

    self.url = url
    self.path = url
    self.query: 'list[str] | None' = None
    self.setUrl(url)

    self.headers = HTTPRequest.Headers()
    self.cookies = dict()
    self.body: bytes | None = None # None or bytes
  
  
  
  #
  # set new request path
  #
  def setUrl(self, url: 'str | None'):
    self.url = url # http request full URL without domain, started from path (str)
    self.path = url # URL path without possible query and fragment (str)
    self.query: 'list[str] | None' = None # URL query if exists (?p1=v1&p2=v2) (None or list of str)

    if url:
      pos = self.path.find('?')
      if pos != -1:
        self.query = self.path[pos + 1:].split('&')
        self.path = self.path[:pos]
  
  
  
  #
  # return "paremeter" fragment from url
  #  for:   'http://example/test?abc=wow&def=lol'
  #  it is: get('abc') -> 'wow'
  #
  def getUrlParam(self, name, default = None) -> 'str | None':
    if self.query is not None:
      beg = name + '='
      
      for par in self.query:
        if par.startswith(beg):
          return par[len(beg):]
    
    return default
  
  #
  # return header or 'default' if not exists
  #  'name' is case insensitive
  #
  def getHeader(self, name: str, default = None) -> 'str | None':
    return self.headers.get(name, default)
  
  #
  # return cookie value or 'default' if not exists
  #
  def getCookie(self, name: str, default = None) -> 'str | None':
    return self.cookies.get(name, default)
  
  #
  # return request body size
  #
  def getBodySize(self) -> int:
    return len(self.body) if self.body else 0

  #
  # return Range header if exists
  #
  def getRange(self):
    range = self.headers.get('Range')
    if range is not None:
      from .respond import HTTPRespond
      return HTTPRespond.RangeValues.parse(range)

    return None

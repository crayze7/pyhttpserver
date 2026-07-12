import time, datetime, gzip, zlib
from .request import HTTPRequest

from typing import TYPE_CHECKING
if TYPE_CHECKING:
  from .protocol import ProtocolBase

#
# optional compression module: lzw3
#
try:
  import lzw3
  has_lzw = True
except ImportError:
  has_lzw = False

#
# optional compression module: brotli
#
try:
  import brotli
  has_brotli = True
except ImportError:
  has_brotli = False



#
# HTTP response data
#
class HTTPRespond:
  def __init__(self):
    self.reset()

  def reset(self) -> None:
    self.status_code = 501
    self.headers: dict[str, str] = dict()
    self.set_cookie: list[str] = list()
    self.content_type: str = None
    self.content_lang: str = None
    self.body: bytes | str = None
    self.cache = 0 # int: 0=no cache, -1=cache for long, x>0=cache for numer of seconds from now
    self.accept_ranges: str | None = 'bytes' # responds with 'Accept-Ranges: bytes'
    self.switch_protocols: 'ProtocolBase | None' = None # used when 101 -> new protocol to upgrade HTTP

  #
  # return header value or 'default' if not exists
  #
  def getHeader(self, name: str, default = None) -> 'str | None':
    return self.headers.get(name, default)
  
  #
  # set header value
  #
  def setHeader(self, name: str, value: str) -> None:
    self.headers[name] = value
  
  #
  # add'Set-Cookie' header and its value
  #
  #  "A <cookie-name> can contain any US-ASCII characters except for: the control character, space, or a tab"
  #  "It also must not contain a separator characters like the following: ( ) < > @ , ; : \ " / [ ] ? = { }."
  #
  #  "A <cookie-value> can optionally be wrapped in double quotes and include any US-ASCII character excluding a control character,"
  #  "Whitespace, double quotes, comma, semicolon, and backslash."
  #
  #  "Indicates the maximum lifetime of the cookie as an HTTP-date timestamp. See Date for the required formatting."
  #  "If unspecified, the cookie becomes a session cookie. A session finishes when the client shuts down, after which the session cookie is removed."
  #
  def setCookie(self, name: str, value: str, expires: 'datetime.datetime | datetime.timedelta | int | None' = None, domain: str = None, path: str = '/', httponly: bool = False, samesite: 'str | None' = 'Lax', secure = False) -> None:
    self.set_cookie.append(HTTPRespond.formatCookie(name, value, expires, domain, path, httponly, samesite, secure))


  @staticmethod
  def formatCookie(name: str, value: str, expires: 'datetime.datetime | datetime.timedelta | int | None' = None, domain: str = None, path: str = '/', httponly: bool = False, samesite: 'str | None' = 'Lax', secure = False) -> str:
    c = name + '=' + value

    if expires is not None:
      if isinstance(expires, datetime.datetime):
        e = expires
      elif isinstance(expires, datetime.timedelta):
        e = datetime.datetime.now() + expires
      elif isinstance(expires, int): # number of seconds
        e = datetime.datetime.fromtimestamp(int(time.time()) + expires)
      else:
        raise TypeError("'expires' type not supported")
      c += '; Expires=' + e.strftime('%a, %d %b %Y %H:%M:%S GMT')

    if domain is not None:
      c += '; Domain=' + domain

    if path is not None:
      c += '; Path=' + path

    if httponly:
      c += '; HttpOnly'

    if samesite is not None:
      if samesite in ('Strict', 'Lax', 'None'):
        c += '; SameSite=' + samesite
      else:
        raise TypeError("'samesite' invalid value")

    if secure:
      c += '; Secure'

    return c



  #
  # range values data
  #
  class RangeValues:
    def __init__(self, f: int, t: 'int | None'):
      self.fr = f # from
      self.to = t # to (may be None)
    
    #
    # parse 'Range' header value
    #
    @staticmethod
    def parse(range: str):
      range = range.replace(' ', '')
      
      if range.startswith('bytes='):
        pos = range.find('-', 6)
        
        if pos != -1:
            fr = range[6:pos]
            to = range[pos + 1:]
            
            if len(fr) > 0 and len(fr) < 50 and fr.isdigit() and (len(to) == 0 or (len(to) < 50 and to.isdigit())):
              return HTTPRespond.RangeValues(int(fr), int(to) if len(to) > 0 else None)
      
      return None
  
  
  
  #
  # postprocessing
  #
  def postprocess(self, request: HTTPRequest, body_encoding: str):
    if self.body:
      if isinstance(self.body, str):
        self.body = self.body.encode(body_encoding, errors='ignore') # convert: str -> bytes

      # possible support for HTTP Range (translation HTTP 200 -> HTTP 206)
      if self.status_code == 200:
        range = request.getHeader('Range')

        if range is not None:
          range = HTTPRespond.RangeValues.parse(range)
          
          if range is not None: # if request contains valid 'Range' header value:
            if range.to is None:
              range.to = len(self.body) - 1
            
            if range.fr < len(self.body) and range.to < len(self.body) and range.fr <= range.to:
              # cut to fit the range size:
              self.status_code = 206 # convert from 200 to 206
              self.setHeader('Content-Range', 'bytes ' + str(range.fr) + '-' + str(range.to) + '/' + str(len(self.body)))
              self.body = self.body[range.fr : range.to + 1]
      

      # support possible http compression for text responses:
      if self.getHeader('Content-Encoding') is None and len(self.body) > 1024: # minimal compression size
        accept_encoding = request.getHeader('Accept-Encoding')
        
        if accept_encoding and self.content_type and self.content_type.startswith('text/'):
          # https://developer.mozilla.org/en-US/docs/Web/HTTP/Headers/Accept-Encoding
          COMPR_GZIP = 1
          COMPR_DEFLATE = 2
          COMPR_BROTLI = 4
          COMPR_LZW = 8
          av_compr = 0 # available compressions flags
          
          for accept_encode in accept_encoding.split(','):
            accept_encode = accept_encode.strip(' ')
            
            if accept_encode.startswith('gzip'):
              av_compr |= COMPR_GZIP # client supports gip
            elif accept_encode.startswith('deflate'):
              av_compr |= COMPR_DEFLATE # client supports deflate
            elif has_brotli and accept_encode.startswith('br'):
              av_compr |= 4 # server and client supports brotli
            elif has_lzw and accept_encode.startswith('compress'):
              av_compr |= 8 # server and client supports lzw3
          
          while av_compr != 0:
            if (av_compr & COMPR_GZIP) == COMPR_GZIP:
              # try gzip compression
              av_compr &= ~COMPR_GZIP
              ok = False
              try:
                self.body = gzip.compress(self.body)
                ok = True
              except Exception:
                pass
              if ok:
                av_compr = 0
                self.setHeader('Content-Encoding', 'gzip')
            
            elif (av_compr & COMPR_DEFLATE) == COMPR_DEFLATE:
              # try deflate compression
              av_compr &= ~COMPR_DEFLATE
              ok = False
              try:
                self.body = zlib.compress(self.body, -1)
                ok = True
              except Exception:
                pass
              if ok:
                av_compr = 0
                self.setHeader('Content-Encoding', 'deflate')
            
            elif (av_compr & COMPR_BROTLI) == COMPR_BROTLI:
              # try brotli compression
              av_compr &= ~COMPR_BROTLI
              ok = False
              try:
                self.body = brotli.compress(self.body)
                ok = True
              except Exception:
                pass
              if ok:
                av_compr = 0
                self.setHeader('Content-Encoding', 'br')
            
            elif (av_compr & COMPR_LZW) == COMPR_LZW:
              # try lzw compression
              av_compr &= ~COMPR_LZW
              ok = False
              try:
                self.body = lzw3.compress(self.body)
                ok = True
              except Exception:
                pass
              if ok:
                av_compr = 0
                self.setHeader('Content-Encoding', 'compress')
            
            else:
              break # Not reachable

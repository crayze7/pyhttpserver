
from typing import TYPE_CHECKING
if TYPE_CHECKING:
  from typing import Callable
  from .request import HTTPRequest
  from .respond import HTTPRespond

  CallableRequestHandler = Callable[[HTTPRequest, HTTPRespond], None | bool]
  CallableErrorHandler = Callable[[HTTPRequest, HTTPRespond, Exception | None], None | bool]



#
# holds list of registered HTTP handlers for specified HTTP method
#
class RegisteredMethodHandlers:
  def __init__(self):
    self._eq: 'dict[str, CallableRequestHandler]' = dict() # path equals
    self._sw: 'dict[str, CallableRequestHandler]' = dict() # path startswith
    self._an: 'set[CallableRequestHandler]' = set() # path any


  def register_eq(self, path: str, handler: 'CallableRequestHandler'):
    prev = self._eq[path] if path in self._eq else None
    self._eq[path] = handler
    return prev


  def register_sw(self, path: str, handler: 'CallableRequestHandler'):
    prev = self._sw[path] if path in self._sw else None
    self._sw[path] = handler
    return prev


  def register_an(self, handler: 'CallableRequestHandler'):
    self._an.add(handler)



  #
  # processing HTTP requests into responses
  #
  def process_request(self, request: 'HTTPRequest', respond: 'HTTPRespond') -> bool:
    if request.path in self._eq:
      (self._eq[request.path])(request, respond)
      return True
    
    for path_start, handler in self._sw.items():
      if request.path.startswith(path_start):
        handler(request, respond)
        return True
    
    for handler in self._an:
      if handler(request, respond):
        return True
    
    return False



  #
  # default processing 404 response
  #
  @staticmethod
  def handler_default404(request: 'HTTPRequest', respond: 'HTTPRespond'):
    respond.status_code = 404
    respond.content_type = 'text/html; charset=utf-8'
    respond.body = b'<!DOCTYPE html><html><head><meta charset="utf-8"><title>404 / Not Found</title></head><body>404 / Not Found</body></html>'

  #
  # default processing 500 response
  #
  @staticmethod
  def handler_default500(request: 'HTTPRequest', respond: 'HTTPRespond', _: 'Exception | None'):
    respond.reset() # reset any previous respond sets
    respond.status_code = 500
    respond.content_type = 'text/html; charset=utf-8'
    respond.body = b'<!DOCTYPE html><html><head><meta charset="utf-8"><title>500 / Internal Server Error</title></head><body>500 / Internal Server Error</body></html>'







#
# holds list of registered HTTP handlers for each method
#
class RegisteredServerHandlers:
  def __init__(self):
    self._def: 'CallableRequestHandler' = RegisteredMethodHandlers.handler_default404 # handler called when path is not found in _registered
    self._err: 'CallableErrorHandler' = RegisteredMethodHandlers.handler_default500 # handler called on uncatched exception from in any other handler in this object
    
    gethead_group = RegisteredMethodHandlers() # GET & HEAD share the same handlers
    self._registered = {
      'HEAD': gethead_group,
      'GET': gethead_group,
      'POST': RegisteredMethodHandlers(),
      'PUT': RegisteredMethodHandlers(),
      'DELETE': RegisteredMethodHandlers(),
      'OPTIONS': RegisteredMethodHandlers(),
      'PATCH': RegisteredMethodHandlers()
    }


  def _register_validate1(self, method: str, handler: 'CallableRequestHandler') -> None:
    if method not in self._registered:
      raise ValueError("Unsupported HTTP method: " + str(method))
    
    if not callable(handler):
      raise TypeError("handler must be callable")


  def _register_validate(self, method: str, path: str, handler: 'CallableRequestHandler') -> None:
    self._register_validate1(method, handler)
    
    if not isinstance(path, str) or not path.startswith('/'):
      raise ValueError("path must be a string / started")
  
  
  def register_eq(self, method: str, path: str, handler: 'CallableRequestHandler') -> None:
    self._register_validate(method, path, handler)
    self._registered[method].register_eq(path, handler)
  
  
  def register_sw(self, method: str, path: str, handler: 'CallableRequestHandler') -> None:
    self._register_validate(method, path, handler)
    self._registered[method].register_sw(path, handler)
  
  
  def register_an(self, method: str, handler: 'CallableRequestHandler') -> None:
    self._register_validate1(method, handler)
    self._registered[method].register_an(handler)
  
  
  
  def set_def(self, handler: 'CallableRequestHandler'):
    if not callable(handler):
      raise TypeError("handler must be callable")
    
    prev = self._def if self._def != RegisteredMethodHandlers.handler_default404 else None
    self._def = handler
    return prev
  
  
  def set_err(self, handler: 'CallableErrorHandler'):
    if not callable(handler):
      raise TypeError("handler must be callable")
    
    prev = self._err if self._err != RegisteredMethodHandlers.handler_default500 else None
    self._err = handler
    return prev
  
  
  #
  # call default handler (Not Found)
  #
  def default(self, request: 'HTTPRequest', respond: 'HTTPRespond') -> None:
    self._def(request, respond)
  

  #
  # call error handler (Internal Server Error)
  #
  def error(self, request: 'HTTPRequest', respond: 'HTTPRespond', e: Exception = None) -> None:
    self._err(request, respond, e)
  
  
  #
  # processing HTTP requests into responses
  #
  def process_request(self, request: 'HTTPRequest', respond: 'HTTPRespond') -> None:
    try:
      if request.method not in self._registered or not self._registered[request.method].process_request(request, respond):
        self.default(request, respond)
    
    except Exception as e:
      self.error(request, respond, e)

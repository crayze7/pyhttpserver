import os, threading, ssl
from pyhttpserver.handlers import RegisteredMethodHandlers # class to holds list of registered HTTP handlers for specified HTTP method
from pyhttpserver.handlers import RegisteredServerHandlers # class to holds list of registered HTTP handlers for each HTTP method
from pyhttpserver.serv import HTTPServer # server interface main class

from typing import TYPE_CHECKING
if TYPE_CHECKING:
  from pyhttpserver.request import HTTPRequest
  from pyhttpserver.respond import HTTPRespond
  from pyhttpserver.interface import HTTPInterface



# if you want to use SSL/TLS you need certificate file and optional key file
HAS_CERT = False
if HAS_CERT:
  certfile = "certfile path"
  certkeyfile = None



#
# if you need HTTP/3 you must specify where to find OpenSSL binaries with minimum 3.5 version:
#  NOTE: make sure that if your python runtime is x64, binaries also must be x64 and vice versa
#
HAS_OPENSSL35 = False # set to True if you set correct paths
if HAS_OPENSSL35:
  crypto3_libpath = "libcrypto-3.dll"
  openssl3_libpath = "libssl-3.dll"


#
# registered HTTP application handlers
#
server_handlers = RegisteredServerHandlers()


#
# register handler to process GET requests that path is equal to: /
#
def http_handler_index(request: 'HTTPRequest', respond: 'HTTPRespond'):
  respond.status_code = 200
  respond.content_lang = 'en-US'
  respond.content_type = 'text/html; charset=utf-8'
  respond.body = b'<!DOCTYPE html><html><head><meta charset="utf-8"><title>Main Page</title></head><body>Main Page Request</body></html>'
  respond.cache = -1 # set browser caching headers: 0=no cache, -1=cache for long, x>0=cache for numer of seconds from now

server_handlers.register_eq('GET', '/', http_handler_index) # handler assigned to path: /



#
# register handler to process POST requests that path start with (or equal): /id
# like: /id  /id359325039  /id5i4954
#
def http_handler_postid(request: 'HTTPRequest', respond: 'HTTPRespond'):
  id = request.path[3:] # remove /id from the request path
  if id == '': # if /id without value:
    RegisteredMethodHandlers.handler_default404(request, respond) # process like 404
    return

  respond.status_code = 200
  respond.content_type = 'text/plain'
  respond.body = str(id) # just return ID value
  respond.cache = 0 # browser should not cache POSTs anyway...
  # optionals:
  respond.setCookie('testcookie', 'value') # you can send Set-Cookie
  respond.setHeader('my-http-header', 'value') # you can respond with custom header

server_handlers.register_sw('POST', '/id', http_handler_postid) # handler assigned to all paths like: /id*



#
# register handler to process all GET requests like: /* but not /
# in this example we redirect everything to /
#
def http_handler_nonindex(request: 'HTTPRequest', respond: 'HTTPRespond') -> bool:
  respond.status_code = 301 # Found
  respond.setHeader('Location', '/') # to full compatibility you should pass full domain URL here, but nowdays browsers supports relative paths anyway
  return True

# handlers registered via register_an() are checked when there is no any "eq" or "sw" handler that match request path
# then all "an" are sequentionaly called until one of them return True which mean request processed
# this is usefull when you have path to process that can't have fixed begin
server_handlers.register_an('GET', http_handler_nonindex)



#
# handler to process unregistered HTTP request (404 Not Found)
#
def http_handler_404(request: 'HTTPRequest', respond: 'HTTPRespond'):
  # format default response as:
  # <!DOCTYPE html><html><head><meta charset="utf-8"><title>404 / Not Found</title></head><body>404 / Not Found</body></html>
  RegisteredMethodHandlers.handler_default404(request, respond)

server_handlers.set_def(http_handler_404) # set http_handler_404() in server_handlers



#
# handler to process expection during processing HTTP request->response
#
def http_handler_500(request: 'HTTPRequest', respond: 'HTTPRespond', e: 'Exception | None'):
  # format default resposne as:
  # <!DOCTYPE html><html><head><meta charset="utf-8"><title>500 / Internal Server Error</title></head><body>500 / Internal Server Error</body></html>
  RegisteredMethodHandlers.handler_default500(request, respond, e)

  if e is not None: # if has Exception instance
    print(e) # print Exception

server_handlers.set_err(http_handler_500) # set http_handler_500() in server_handlers





#
# example of application HTTP server instance
#
class AppHttpServer(HTTPServer):
  def __init__(self):
    super().__init__()
    self.handlers = server_handlers
    self.appstop = threading.Event() # used to clean stop this server


  #
  # processing HTTP requests into responses
  # called once for each request and from different threads (for HTTP1 from connection thread for 2/3 from unique threads)
  #
  def process_request(self, request: 'HTTPRequest', respond: 'HTTPRespond') -> None:
    print(str(request.interface) + " " + request.addr + " " + request.method + " " + request.url) # log request first
    self.handlers.process_request(request, respond) # process respond by 'server_handlers' instance


  #
  # this method must return 'Server' header value as str
  # called once for each request and from different threads
  #
  def conf_server_header(self):
    return "pyhttpserver/example"

  #
  # default Content-Language hader value (used when HTTPRequest. is None)
  #
  def conf_default_content_lang(self):
    return "en-US"


  #
  # if you want to use SSL/TLS - initialize its ssl.SSLContext for HTTP1 and HTTP/2
  #
  def init_ssl(self, sslctx: 'ssl.SSLContext'):
    sslctx.load_cert_chain(certfile, certkeyfile) # your setup here <-------


  #
  # start listening on specific interfaces
  #
  def start_listening(self) -> None:
    self.startListeningTCPIPv4HTTP(80, "0.0.0.0") # start listening on HTTP default port from any incoming IPv4
    self.startListeningTCPIPv6HTTP(80, "::") # start listening on HTTP default port from any incoming IPv6

    if HAS_CERT:
      self.startListeningTCPIPv4HTTPS(443, "0.0.0.0") # start listening on HTTPS default port from any incoming IPv4
      self.startListeningTCPIPv6HTTPS(443, "::") # start listening on HTTPS default port from any incoming IPv6

      if HAS_OPENSSL35:
        # need to prepare HTTP3 QUIC OpenSSL runtime here:
        self.prepareHTTP3(openssl3_libpath, crypto3_libpath, certfile, certkeyfile) # your setup here <-------
        self.startListeningQuicIPv4HTTP3(443, "0.0.0.0") # start listening UDP QUIC connections from any incoming IPv4
        self.startListeningQuicIPv6HTTP3(443, "::") # start listening UDP QUIC connections from any incoming IPv6

    self.startAccepting() # start accepting incoming connections


  #
  # called when any listening interface failed from some reason
  #
  def listen_failed(self, interface: 'HTTPInterface'):
    print("HTTP listening failed on interface: " + str(interface))
    self.appstop.set() # initialize stopping whole server


  #
  # log exception happend during server request processing
  #
  def report_exception(self, e: Exception, request: 'HTTPRequest | None') -> None:
    print(e, "REQUEST: " + request.method + " " + request.url)


  #
  # log event
  #
  def report_event(self, type: str, thread_id: int, args: list) -> None:
    print(type + ' ' + str(thread_id), ' '.join(map(str, args)))


  #
  # HTTP3/QUIC extension
  # may return "Alt-Svc" headers
  # https://www.rfc-editor.org/rfc/rfc7838.html#section-4
  # https://www.rfc-editor.org/rfc/rfc9114.html#name-http-alternative-services
  #
  def alt_svc(self, version: int) -> 'list[str] | str | None':
    return None





#
# set stop handler
#
def handler_stop():
  serv.appstop.set() # it will break serv.appstop.wait() below

if os.name == 'nt':
  from ctypes import WINFUNCTYPE, windll
  from ctypes.wintypes import BOOL, DWORD
  PHANDLER_ROUTINE = WINFUNCTYPE(BOOL, DWORD)

  @PHANDLER_ROUTINE
  def handler_stop_win(type):
    if type in (0, 1, 2, 5, 6):
      handler_stop()
      return 1
    return 0

  SetConsoleCtrlHandler = windll.LoadLibrary("kernel32").SetConsoleCtrlHandler
  SetConsoleCtrlHandler.argtypes = (PHANDLER_ROUTINE, BOOL)
  SetConsoleCtrlHandler.restype = BOOL
  SetConsoleCtrlHandler(handler_stop_win, True)

else:
  from signal import *
  signal(SIGQUIT, handler_stop)
  signal(SIGINT, handler_stop)





# running single instance:
serv = AppHttpServer()
serv.start_listening()

print("Running...")
serv.appstop.wait() # wait till something want to stop the server

# clean shutting down:
print("Stopping...")
serv.stopListening() # stop listening interfaces
serv.stopConnections() # start closing existed active connections
serv.joinConnections() # wait to stop all threads and connections
print("Exit")

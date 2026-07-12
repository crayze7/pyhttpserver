import threading, socket, ssl
from .interface import HTTPInterface, HTTPInterfaceTCPIPv4HTTP, HTTPInterfaceTCPIPv4HTTPS, HTTPInterfaceTCPIPv6HTTP, HTTPInterfaceTCPIPv6HTTPS, HTTPInterfaceUDPIPv4HTTPS, HTTPInterfaceUDPIPv6HTTPS
from .listen import HTTPListenTCP
from .http3.libopenssl3 import OpenSSL, libopenssl_load, libopenssl_loaded
from .http3.listen import HTTPListenQUIC

from typing import TYPE_CHECKING
if TYPE_CHECKING:
  from .request import HTTPRequest
  from .respond import HTTPRespond
  from .listen import HTTPListen
  from .connection import HTTPConnection



#
# http server instance base class
#
class HTTPServer:
  def __init__(self, http2_enable: bool = True):
    self._sslctx: 'ssl.SSLContext | None' = None # Python's SSL context (HTTP/1 nad HTTP/2 only)
    self._openssl: 'OpenSSL.CTX | None' = None # OpenSSL library context (HTTP/3 only)
    self._listens : 'set[HTTPListen]' = set() # http listening collection (set of HTTPListen objects)
    self._connections_access = threading.Lock() # connections access guard
    self._connections_set : 'set[HTTPConnection]' = set() # http connections collection (set of HTTPConnection objects)
    self._http2_enable = http2_enable


  #
  # precessing HTTP request callback
  # - called in any request processing thread
  # - 'method' is string like 'HEAD', 'GET', 'POST', ...
  # - 'request' must be readonly
  # - 'respond' should be set in response
  #
  def process_request(self, request: 'HTTPRequest', respond: 'HTTPRespond') -> None:
    raise NotImplementedError(
        "Method Server::process_request() must be reimplemented")

  #
  # setup SSL context callback
  #  called internally in startListening*HTTPS() on first HTTPS interface initialization
  #  - sslctx.load_cert_chain() should be called
  #
  def init_ssl(self, sslctx: ssl.SSLContext) -> None:
    raise NotImplementedError(
        "Method Server::init_ssl() must be reimplemented")


  #
  # reports that accepting thread stopped with issue
  #  called from any listening thread
  #
  def listen_failed(self, interface: HTTPInterface):
    pass # silent ignore

  #
  # events reporting callback
  #  type values:
  #   TCP activity: 'TCP.START', 'TCP.CLOSE', 'TCP.ERROR'
  #   HTTP activity: 'HTTP1.REQ', 'HTTP1.RES'
  #
  def report_event(self, type: str, thread_id: int, args: list) -> None:
    pass # silent ignore

  #
  # callback to report exception from connection thread
  #
  def report_exception(self, e: Exception, request: 'HTTPRequest | None') -> None:
    pass # silent ignore



  #
  # Server HTTP header value (MUST BE ascii encoded)
  #
  def conf_server_header(self) -> str:
    return 'HTTPServer/Python'

  #
  # encoding value used when to convert body response from 'str' to 'bytes'
  #
  def conf_body_encoding(self) -> str:
    return 'utf-8'

  #
  # default content language
  #
  def conf_default_content_lang(self) -> 'str | None':
    return None

  #
  # HTTP3/QUIC extension
  # may return "Alt-Svc" headers
  # https://www.rfc-editor.org/rfc/rfc7838.html#section-4
  # https://www.rfc-editor.org/rfc/rfc9114.html#name-http-alternative-services
  #
  def alt_svc(self, version: int) -> 'list[str] | str | None':
    return None



  #
  # starts listening on TCP/IPv4/HTTP interface
  #
  def startListeningTCPIPv4HTTP(self, listen_port: int = 80, listen_addr_ipv4: str = "0.0.0.0") -> None:
    self._listens.add(HTTPListenTCP(self, HTTPInterfaceTCPIPv4HTTP(), socket.AF_INET, listen_addr_ipv4, listen_port))
  
  #
  # starts listening on TCP/IPv4/HTTPS interface
  #
  def startListeningTCPIPv4HTTPS(self, listen_port: int = 443, listen_addr_ipv4: str = "0.0.0.0") -> None:
    self._listens.add(HTTPListenTCP(self, HTTPInterfaceTCPIPv4HTTPS(), socket.AF_INET, listen_addr_ipv4, listen_port, self._get_sslctx()))
  
  #
  # starts listening on TCP/IPv6/HTTP interface
  #
  def startListeningTCPIPv6HTTP(self, listen_port: int = 80, listen_addr_ipv6: str = "::") -> None:
    self._listens.add(HTTPListenTCP(self, HTTPInterfaceTCPIPv6HTTP(), socket.AF_INET6, listen_addr_ipv6, listen_port))
  
  #
  # starts listening on TCP/IPv6/HTTPS interface
  #
  def startListeningTCPIPv6HTTPS(self, listen_port: int = 443, listen_addr_ipv6: str = "::") -> None:
    self._listens.add(HTTPListenTCP(self, HTTPInterfaceTCPIPv6HTTPS(), socket.AF_INET6, listen_addr_ipv6, listen_port, self._get_sslctx()))


  #
  # prepare OpenSSL runtime to support QUIC HTTP/3
  #  must be called before any startListeningQuic*() call
  #
  def prepareHTTP3(self, openssl_libpath: str, crypto_libpath: str, certfile: str, keyfile: str = '', debug_log: str = None):
    if not libopenssl_loaded():
      libopenssl_load(openssl_libpath, crypto_libpath)

    if self._openssl is None:
      self._openssl = OpenSSL.CTX.new_quic_server()

      try:
        if debug_log: # experimental runtime - debug problems - create OpenSSL log file:
          self._openssl.open_debug_logfile(debug_log)

        self._openssl.set_verify(OpenSSL.SSL_VERIFY_NONE, None) # TODO
        self._openssl.set_options(OpenSSL.SSL_OP_ALL | OpenSSL.SSL_OP_IGNORE_UNEXPECTED_EOF)

        self._openssl.use_certificate_chain_file(certfile)
        if keyfile:
          self._openssl.use_private_key_file(keyfile, OpenSSL.SSL_FILETYPE_PEM)

      except Exception:
        self._openssl.destroy()
        self._openssl = None
        raise

  #
  # starts listening on UDP:QUIC/IPv4/HTTPS interface
  #
  def startListeningQuicIPv4HTTP3(self, listen_port: int = 443, listen_addr_ipv4: str = "0.0.0.0") -> None:
    if self._openssl is None:
      raise RuntimeError("prepareHTTP3() must be called first")

    self._listens.add(HTTPListenQUIC(self, HTTPInterfaceUDPIPv4HTTPS(), socket.AF_INET, listen_addr_ipv4, listen_port)) # TODO experimental

  #
  # starts listening on UDP:QUIC/IPv6/HTTPS interface
  #
  def startListeningQuicIPv6HTTP3(self, listen_port: int = 443, listen_addr_ipv6: str = "::") -> None:
    if self._openssl is None:
      raise RuntimeError("prepareHTTP3() must be called first")

    self._listens.add(HTTPListenQUIC(self, HTTPInterfaceUDPIPv6HTTPS(), socket.AF_INET6, listen_addr_ipv6, listen_port)) # TODO experimental


  #
  # starts threads which accepts connections
  #
  def startAccepting(self):
    for listen in self._listens:
      listen.start()
  
  
  #
  # stops listening interfaces
  #  when this method returns listening sockets are closed and threads stopped
  #  
  #
  def stopListening(self):
    for listen in self._listens:
      listen.stop()
    
    for listen in self._listens:
      listen.join()
    
    self._listens.clear()


  #
  # stops existed http clients connections
  #
  def stopConnections(self):
    with self._connections_access:
      for conn in self._connections_set:
        conn.stop()

  #
  # join clients connections
  #
  def joinConnections(self):
    with self._connections_access:
      connections = list(self._connections_set)

    for conn in connections:
      conn.join()





  #
  # get/create ssl context
  #
  def _get_sslctx(self) -> ssl.SSLContext:
    if self._sslctx is None:
      self._sslctx = ssl.SSLContext(ssl.PROTOCOL_TLS_SERVER)
      self._sslctx.verify_mode = ssl.CERT_REQUIRED
      self.init_ssl(self._sslctx)
      
      if self._http2_enable:
        self._sslctx.set_alpn_protocols(( 'h2', 'http/1.1'))
      else:
        self._sslctx.set_alpn_protocols(( 'http/1.1'))
    
    return self._sslctx
  
  
  #
  # starts new http client connection handing
  #  - called from any listening thread
  #
  def _start_connection(self, conn: 'HTTPConnection') -> None:
    with self._connections_access:
      self._connections_set.add(conn)

    conn.start()
  
  
  #
  # remove http client connection
  #  - called from any client connection thread
  #  - socket is already destroyed on this call
  #
  def _remove_connection(self, conn: 'HTTPConnection') -> None:
    with self._connections_access:
      self._connections_set.discard(conn)

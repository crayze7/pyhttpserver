
TCP = 'TCP'
UDP = 'UDP'
IPV4 = 'IPv4'
IPV6 = 'IPv6'
HTTP = 'HTTP'
HTTPS = 'HTTPS'
WEBSOCKET = 'WebSocket'

#
# listening HTTP interface
#
class HTTPInterface:
  def __init__(self):
    self.inet = '-'
    self.ip = '-'
    self.proto = '-'
  
  
  def __repr__(self) -> str:
    return self.inet + '/' + self.ip + '/' + self.proto
  
  def __str__(self) -> str:
    return self.__repr__()
  
  
  def is_tcp(self) -> bool:
    return self.inet == TCP
  
  def is_udp(self) -> bool:
    return self.inet == UDP
  
  def is_ipv4(self) -> bool:
    return self.ip == IPV4
  
  def is_ipv6(self) -> bool:
    return self.ip == IPV6
  
  def is_http(self) -> bool:
    return self.proto == HTTP
  
  def is_https(self) -> bool :
    return self.proto == HTTPS
  
  def is_websocket(self) -> bool:
    return self.proto == WEBSOCKET



class HTTPInterfaceTCP(HTTPInterface):
  def __init__(self):
    super().__init__()
    self.inet = TCP

class HTTPInterfaceTCPIPv4(HTTPInterfaceTCP):
  def __init__(self):
    super().__init__()
    self.ip = IPV4

class HTTPInterfaceTCPIPv6(HTTPInterfaceTCP):
  def __init__(self):
    super().__init__()
    self.ip = IPV6

class HTTPInterfaceTCPIPv4HTTP(HTTPInterfaceTCPIPv4):
  def __init__(self):
    super().__init__()
    self.proto = HTTP

class HTTPInterfaceTCPIPv4HTTPS(HTTPInterfaceTCPIPv4):
  def __init__(self):
    super().__init__()
    self.proto = HTTPS

class HTTPInterfaceTCPIPv6HTTP(HTTPInterfaceTCPIPv6):
  def __init__(self):
    super().__init__()
    self.proto = HTTP

class HTTPInterfaceTCPIPv6HTTPS(HTTPInterfaceTCPIPv6):
  def __init__(self):
    super().__init__()
    self.proto = HTTPS



class HTTPInterfaceWebSocket(HTTPInterface):
  def __init__(self, upgrade_from: HTTPInterface):
    super().__init__()
    self.inet = upgrade_from.inet
    self.ip = upgrade_from.ip
    self.proto = WEBSOCKET



class HTTPInterfaceUDP(HTTPInterface):
  def __init__(self):
    super().__init__()
    self.inet = UDP

class HTTPInterfaceUDPIPv4(HTTPInterfaceUDP):
  def __init__(self):
    super().__init__()
    self.ip = IPV4

class HTTPInterfaceUDPIPv6(HTTPInterfaceUDP):
  def __init__(self):
    super().__init__()
    self.ip = IPV6

class HTTPInterfaceUDPIPv4HTTPS(HTTPInterfaceUDPIPv4):
  def __init__(self):
    super().__init__()
    self.proto = HTTPS

class HTTPInterfaceUDPIPv6HTTPS(HTTPInterfaceUDPIPv6):
  def __init__(self):
    super().__init__()
    self.proto = HTTPS

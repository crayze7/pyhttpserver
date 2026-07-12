from typing import TYPE_CHECKING
if TYPE_CHECKING:
  from .connection import HTTPConnectionTCP


#
# all methods are called in 'connection recv thread'
#
class ProtocolBase:
  #
  # use TCP connection with this instance
  #
  def use_connection(self, conn: 'HTTPConnectionTCP') -> None:
    pass

  #
  # called when requested to close the connection
  #
  def close_request(self) -> bool:
    return False # default: stop connection

  #
  # check called every 50ms, can be used to any maintance
  #
  def check(self) -> bool:
    return True # default: continue connection

  #
  # returns max idle timeout (in seconds) for connection
  #  server disconnects conection after that time if there was no income bytes (no activity in recv)
  #  0 is disabled: no timeout
  #
  def idle_timeout(self) -> float:
    return 0

  #
  # called by connection on incoming bytes
  #
  def recv(self, stream: bytes) -> 'bool | ProtocolBase':
    return False # default: stop connection


  #
  # called when connection is closing, right before close the connection socket
  #  'close_reason' is one of the HTTPConnection.CLOSE_REASON_* values
  #
  def close(self, close_reason: int) -> None:
    pass


  #
  # called when connection is discarded because of switched by other ProtocolBase instance
  #  'by' is new protocol
  #
  def switchby(self, by: 'ProtocolBase'):
    pass

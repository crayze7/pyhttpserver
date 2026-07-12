import sys, math, ctypes, socket

#
# socket utility - implements socket "things" missing in python socket implementation
#



#
# set socket timeout on recv() and send() separately
#
def socket_settimeout(sock: 'socket.socket', what: int, timeout: 'float | None'):
  if sys.platform.startswith('win'): # if win32 SO_RCVTIMEO/SO_SNDTIMEO value is DWORD in miliseconds: https://learn.microsoft.com/en-us/windows/win32/winsock/sol-socket-socket-options
    v = max(round(timeout * 1000), 1) if timeout is not None and timeout > 0 else 0
    v = v.to_bytes(4, byteorder=sys.byteorder, signed=False) # int -> DWORD

  else: # if linux SO_RCVTIMEO/SO_SNDTIMEO value is: struct timeval { time_t tv_sec; suseconds_t tv_usec; };
    if timeout is not None and timeout > 0:
      usec, sec = math.modf(timeout)
      usec = min(round(usec * 1000000), 999999) # fractional -> usec
      sec = math.trunc(sec) # float -> int
      if sec == 0 and usec == 0:
        usec = 1
    else:
      sec = 0
      usec = 0

    l = ctypes.sizeof(ctypes.c_void_p) # sizeof time_t and suseconds_t
    v = sec.to_bytes(l, byteorder=sys.byteorder, signed=False) + usec.to_bytes(l, byteorder=sys.byteorder, signed=False)

  sock.setsockopt(socket.SOL_SOCKET, what, v)

#
# set socket timeout on recv() only
#
def socket_settimeout_recv(sock: 'socket.socket', timeout: 'float | None'):
  socket_settimeout(sock, socket.SO_RCVTIMEO, timeout)

#
# set socket timeout on send() only
#
def socket_settimeout_send(sock: 'socket.socket', timeout: 'float | None'):
  socket_settimeout(sock, socket.SO_SNDTIMEO, timeout)





#
# enable and setup TCP KEEP ALIVE behaviour
#  idle:  The time (in seconds) the connection needs to remain idle before TCP starts sending keepalive probes
#  intvl: The time (in seconds) between individual keepalive probes
#  cnt:   The maximum number of keepalive probes TCP should send before dropping the connection
#
def socket_tcp_set_keepalive(sock: 'socket.socket', idle: int, intvl: int, cnt: int):
  if hasattr(sock, 'ioctl') and hasattr(socket, 'SIO_KEEPALIVE_VALS'): # if windows sockets:
    sock.ioctl(socket.SIO_KEEPALIVE_VALS, (1, idle * 1000, intvl * 1000)) # (enabled, TCP_KEEPIDLE in ms, TCP_KEEPINTVL in ms)
    sock.setsockopt(socket.IPPROTO_TCP, socket.TCP_KEEPCNT, cnt) # The maximum number of keepalive probes TCP should send before dropping the connection

  else: # if linux:
    sock.setsockopt(socket.SOL_TCP, socket.TCP_KEEPIDLE, idle) # The time (in seconds) the connection needs to remain idle before TCP starts sending keepalive probes
    sock.setsockopt(socket.SOL_TCP, socket.TCP_KEEPINTVL, intvl) # The time (in seconds) between individual keepalive probes
    sock.setsockopt(socket.SOL_TCP, socket.TCP_KEEPCNT, cnt) # The maximum number of keepalive probes TCP should send before dropping the connection

  sock.setsockopt(socket.SOL_SOCKET, socket.SO_KEEPALIVE, 1)

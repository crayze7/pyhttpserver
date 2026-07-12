import os
from ctypes import *

#
# based on:
#  https://docs.openssl.org/3.5/man7/ossl-guide-quic-server-block/
#  https://docs.openssl.org/3.5/man7/openssl-quic/
#
# OpenSSL starting from 3.2 has QUIC protocol support for client and from 3.5 for server side
#

SSL_METHOD = c_void_p
SSL_CTX = c_void_p
SSL = c_void_p
BIO = c_void_p
BIO_METHOD = c_void_p

# typedef int (*ssl_ctx_alpn_select_proc) (SSL *ssl, const unsigned char **out, unsigned char *outlen, const unsigned char *in, unsigned int inlen, void *arg);
ssl_ctx_alpn_select_proc = CFUNCTYPE(c_int, SSL, c_void_p, c_void_p, POINTER(c_byte), c_uint, c_void_p)

# typedef void (*ssl_ctx_msg_proc)(int write_p, int version, int content_type, const void *buf, size_t len, SSL *ssl, void *arg);
ssl_ctx_msg_proc = CFUNCTYPE(None, c_int, c_int, c_int, c_void_p, c_size_t, SSL, c_void_p)

# typedef int (*error_bio_write_proc)(BIO *, const char *, int);
error_bio_write_proc = CFUNCTYPE(c_int, BIO, c_void_p, c_int)


# typedef struct ssl_stream_reset_args_st {
#   uint64_t quic_error_code;
# } SSL_STREAM_RESET_ARGS;
class SSL_STREAM_RESET_ARGS(Structure):
  _fields_ = [( "quic_error_code", c_uint64 )]


#typedef struct ssl_shutdown_ex_args_st {
#    uint64_t    quic_error_code;
#    const char  *quic_reason;
#} SSL_SHUTDOWN_EX_ARGS;
class SSL_SHUTDOWN_EX_ARGS(Structure):
  _fields_ = [
    ( "quic_error_code", c_int64 ),
    ( "quic_reason", c_char_p )
  ]


libcrypto = None
libopenssl = None

#
# check does OpenSSL library (and its libcrypto dependency) is loaded
#
def libopenssl_loaded():
  global libcrypto, libopenssl
  return libcrypto is not None and libopenssl is not None

#
# load OpenSSL lib min version is 3.5
#
def libopenssl_load(libpath: str, cryptopath: str):
  if libopenssl_loaded():
    return

  global libopenssl, libcrypto
  libopenssl = cdll.LoadLibrary(libpath)
  libcrypto = cdll.LoadLibrary(cryptopath)

  if not hasattr(libopenssl, 'OSSL_QUIC_server_method'):
    raise RuntimeError("OpenSSL 3.5 is minimal required version")

  # added in OpenSSL 3.5:
  # const SSL_METHOD *OSSL_QUIC_server_method(void);
  libopenssl.OSSL_QUIC_server_method.argtypes = []
  libopenssl.OSSL_QUIC_server_method.restype = SSL_METHOD

  # SSL_CTX *SSL_CTX_new(const SSL_METHOD *method);
  libopenssl.SSL_CTX_new.argtypes = [ SSL_METHOD ]
  libopenssl.SSL_CTX_new.restype = SSL_CTX

  # void SSL_CTX_free(SSL_CTX *ctx);
  libopenssl.SSL_CTX_free.argtypes = [ SSL_CTX ]
  libopenssl.SSL_CTX_free.restype = None

  # int SSL_CTX_use_certificate_chain_file(SSL_CTX *ctx, const char *file);
  libopenssl.SSL_CTX_use_certificate_chain_file.argtypes = [ SSL_CTX, c_char_p ]
  libopenssl.SSL_CTX_use_certificate_chain_file.restype = c_int

  # int SSL_CTX_use_PrivateKey_file(SSL_CTX *ctx, const char *file, int type);
  libopenssl.SSL_CTX_use_PrivateKey_file.argtypes = [ SSL_CTX, c_char_p, c_int ]
  libopenssl.SSL_CTX_use_PrivateKey_file.restype = c_int

  # void SSL_CTX_set_verify(SSL_CTX *ctx, int mode, SSL_verify_cb verify_callback);
  libopenssl.SSL_CTX_set_verify.argtypes = [ SSL_CTX, c_int, c_void_p ]
  libopenssl.SSL_CTX_set_verify.restype = None

  # void SSL_CTX_set_alpn_select_cb(SSL_CTX *ctx, int (*cb) (SSL *ssl, const unsigned char **out, unsigned char *outlen, const unsigned char *in, unsigned int inlen, void *arg), void *arg);
  libopenssl.SSL_CTX_set_alpn_select_cb.argtypes = [ SSL_CTX, ssl_ctx_alpn_select_proc, c_void_p ]
  libopenssl.SSL_CTX_set_alpn_select_cb.restype = None

  # uint64_t SSL_CTX_set_options(SSL_CTX *ctx, uint64_t options);
  libopenssl.SSL_CTX_set_options.argtypes = [ SSL_CTX, c_uint64 ]
  libopenssl.SSL_CTX_set_options.restype = c_uint64

  # void SSL_CTX_set_msg_callback(SSL_CTX *ctx, void (*cb)(int write_p, int version, int content_type, const void *buf, size_t len, SSL *ssl, void *arg));
  libopenssl.SSL_CTX_set_msg_callback.argtypes = [ SSL_CTX, ssl_ctx_msg_proc ]
  libopenssl.SSL_CTX_set_msg_callback.restype = None

  # int SSL_select_next_proto(unsigned char **out, unsigned char *outlen, const unsigned char *server, unsigned int server_len, const unsigned char *client, unsigned int client_len);
  libopenssl.SSL_select_next_proto.argtypes = [ c_void_p, c_void_p, POINTER(c_byte), c_int, c_void_p, c_int ]
  libopenssl.SSL_select_next_proto.restype = c_int

  # long SSL_CTX_ctrl(SSL_CTX *ctx, int cmd, long larg, void *parg);
  libopenssl.SSL_CTX_ctrl.argtypes = [ SSL_CTX, c_int, c_long, c_void_p ]
  libopenssl.SSL_CTX_ctrl.restype = c_long

  # SSL *SSL_new_listener(SSL_CTX *ctx, uint64_t flags);
  libopenssl.SSL_new_listener.argtypes = [ SSL_CTX, c_uint64 ]
  libopenssl.SSL_new_listener.restype = SSL

  # void SSL_free(SSL *ssl);
  libopenssl.SSL_free.argtypes = [ SSL ]
  libopenssl.SSL_free.restype = None

  # int SSL_set_fd(SSL *s, int fd);
  libopenssl.SSL_set_fd.argtypes = [  ]
  libopenssl.SSL_set_fd.restype = c_int

  # int SSL_listen(SSL *ssl);
  libopenssl.SSL_listen.argtypes = [ SSL ]
  libopenssl.SSL_listen.restype = c_int

  # SSL *SSL_accept_connection(SSL *ssl, uint64_t flags);
  libopenssl.SSL_accept_connection.argtypes = [ SSL, c_uint64 ]
  libopenssl.SSL_accept_connection.restype = SSL

  # int SSL_set_default_stream_mode(SSL *conn, uint32_t mode);
  libopenssl.SSL_set_default_stream_mode.argtypes = [ SSL, c_uint32 ]
  libopenssl.SSL_set_default_stream_mode.restype = c_int

  # int SSL_set_incoming_stream_policy(SSL *conn, int policy, uint64_t app_error_code);
  libopenssl.SSL_set_incoming_stream_policy.argtypes = [ SSL, c_int, c_uint64 ]
  libopenssl.SSL_set_incoming_stream_policy.restype = c_int

  # SSL *SSL_new_stream(SSL *ssl, uint64_t flags);
  libopenssl.SSL_new_stream.argtypes = [ SSL, c_uint64 ]
  libopenssl.SSL_new_stream.restype = SSL

  # SSL *SSL_accept_stream(SSL *ssl, uint64_t flags);
  libopenssl.SSL_accept_stream.argtypes = [ SSL, c_uint64 ]
  libopenssl.SSL_accept_stream.restype = SSL

  # int SSL_get_stream_type(SSL *ssl);
  libopenssl.SSL_get_stream_type.argtypes = [ SSL ]
  libopenssl.SSL_get_stream_type.restype = c_int

  # uint64_t SSL_get_stream_id(SSL *ssl);
  libopenssl.SSL_get_stream_id.argtypes = [ SSL ]
  libopenssl.SSL_get_stream_id.restype = c_uint64

  # int SSL_read(SSL *ssl, void *buf, int num);
  libopenssl.SSL_read.argtypes = [ SSL, c_void_p, c_int ]
  libopenssl.SSL_read.restype = c_int

  # int SSL_write(SSL *ssl, const void *buf, int num);
  libopenssl.SSL_write.argtypes = [ SSL, c_void_p, c_int ]
  libopenssl.SSL_write.restype = c_int

  # int SSL_stream_conclude(SSL *s, uint64_t flags);
  libopenssl.SSL_stream_conclude.argtypes = [ SSL, c_uint64 ]
  libopenssl.SSL_stream_conclude.restype = c_int

  # int SSL_stream_reset(SSL *ssl, const SSL_STREAM_RESET_ARGS *args, size_t args_len);
  libopenssl.SSL_stream_reset.argtypes = [ SSL, POINTER(SSL_STREAM_RESET_ARGS), c_size_t ]
  libopenssl.SSL_stream_reset.restype = c_int

  # int SSL_get_error(const SSL *ssl, int ret);
  libopenssl.SSL_get_error.argtypes = [ SSL, c_int ]
  libopenssl.SSL_get_error.restype = c_int

  # int SSL_shutdown(SSL *ssl);
  libopenssl.SSL_shutdown.argtypes = [ SSL ]
  libopenssl.SSL_shutdown.restype = c_int

  # int SSL_shutdown_ex(SSL *ssl, uint64_t flags, const SSL_SHUTDOWN_EX_ARGS *args, size_t args_len);
  libopenssl.SSL_shutdown_ex.argtypes = [ SSL, c_uint64, POINTER(SSL_SHUTDOWN_EX_ARGS), c_size_t ]
  libopenssl.SSL_shutdown_ex.restype = c_int

  # void ERR_clear_error(void);
  libcrypto.ERR_clear_error.argtypes = []
  libcrypto.ERR_clear_error.restype = None

  # unsigned long ERR_peek_error(void);
  libcrypto.ERR_peek_error.argtypes = []
  libcrypto.ERR_peek_error.restype = c_ulong

  # unsigned long ERR_peek_last_error(void);
  libcrypto.ERR_peek_last_error.argtypes = []
  libcrypto.ERR_peek_last_error.restype = c_ulong

  # void ERR_print_errors(BIO *bp);
  libcrypto.ERR_print_errors.argtypes = [ BIO ]
  libcrypto.ERR_print_errors.restype = None

  # BIO_METHOD *BIO_meth_new(int type, const char *name);
  libcrypto.BIO_meth_new.argtypes = [ c_int, c_char_p ]
  libcrypto.BIO_meth_new.restype = BIO_METHOD

  # void BIO_meth_free(BIO_METHOD *biom);
  libcrypto.BIO_meth_free.argtypes = [ BIO_METHOD ]
  libcrypto.BIO_meth_free.restype = None

  # int BIO_meth_set_write(BIO_METHOD *biom, write_proc write);
  libcrypto.BIO_meth_set_write.argtypes = [ BIO_METHOD, error_bio_write_proc ]
  libcrypto.BIO_meth_set_write.restype = c_int

  # BIO *BIO_new(const BIO_METHOD *type);
  libcrypto.BIO_new.argtypes = [ BIO_METHOD ]
  libcrypto.BIO_new.restype = BIO

  # void BIO_free_all(BIO *a);
  libcrypto.BIO_free_all.argtypes = [ BIO ]
  libcrypto.BIO_free_all.restype = None

  # void *BIO_get_data(BIO *a);
  libcrypto.BIO_get_data.argtypes = [ BIO ]
  libcrypto.BIO_get_data.restype = c_void_p

  # void BIO_set_data(BIO *a, void *ptr);
  libcrypto.BIO_set_data.argtypes = [ BIO, c_void_p ]
  libcrypto.BIO_set_data.restype = None

  # void BIO_set_init(BIO *a, int init);
  libcrypto.BIO_set_init.argtypes = [ BIO, c_int ]
  libcrypto.BIO_set_init.restype = None

  # int BIO_socket_nbio(int sock, int mode);
  libcrypto.BIO_socket_nbio.argtypes = [ c_int, c_int ]
  libcrypto.BIO_socket_nbio.restype = c_int

  # int BIO_closesocket(int sock);
  libcrypto.BIO_closesocket.argtypes = [ c_int ]
  libcrypto.BIO_closesocket.restype = c_int



def OSSL_QUIC_server_method() -> SSL_METHOD:
  return libopenssl.OSSL_QUIC_server_method()

def SSL_CTX_new(method: SSL_METHOD) -> SSL_CTX:
  return libopenssl.SSL_CTX_new(method)

def SSL_CTX_free(ctx: SSL_CTX) -> None:
  libopenssl.SSL_CTX_free(ctx)

def SSL_CTX_use_certificate_chain_file(ctx: SSL_CTX, file: str) -> int:
  return libopenssl.SSL_CTX_use_certificate_chain_file(ctx, file.encode())

def SSL_CTX_use_PrivateKey_file(ctx: SSL_CTX, file: str, type: int) -> int:
  return libopenssl.SSL_CTX_use_PrivateKey_file(ctx, file.encode(), type)

def SSL_CTX_set_verify(ctx: SSL_CTX, mode: int, arg: c_void_p) -> None:
  libopenssl.SSL_CTX_set_verify(ctx, mode, arg)

def SSL_CTX_set_alpn_select_cb(ctx: SSL_CTX, cb, arg: c_void_p) -> None:
  libopenssl.SSL_CTX_set_alpn_select_cb(ctx, cb, arg)

def SSL_CTX_set_options(ctx: SSL_CTX, options: int):
  return libopenssl.SSL_CTX_set_options(ctx, options)

def SSL_CTX_set_msg_callback(ctx: SSL_CTX, cb) -> None:
  libopenssl.SSL_CTX_set_msg_callback(ctx, cb)

def SSL_CTX_set_msg_callback_arg(ctx: SSL_CTX, arg: c_void_p):
  # define SSL_CTX_set_msg_callback_arg(ctx, arg) SSL_CTX_ctrl((ctx), SSL_CTRL_SET_MSG_CALLBACK_ARG, 0, (arg))
  libopenssl.SSL_CTX_ctrl(ctx, 16, 0, arg) # SSL_CTRL_SET_MSG_CALLBACK_ARG = 16

def SSL_select_next_proto(out: c_void_p, out_len: c_void_p, server, server_len: int, client, client_len: int) -> int:
  return libopenssl.SSL_select_next_proto(out, out_len, server, server_len, client, client_len)

def SSL_new_listener(ctx: SSL_CTX, flags: int) -> SSL:
  return libopenssl.SSL_new_listener(ctx, flags)

def SSL_free(ssl: SSL) -> None:
  libopenssl.SSL_free(ssl)

def SSL_set_fd(ssl: SSL, fd) -> int:
  return libopenssl.SSL_set_fd(ssl, fd)

def SSL_listen(ssl: SSL) -> int:
  return libopenssl.SSL_listen(ssl)

def SSL_accept_connection(ssl: SSL, flags: int) -> SSL:
  return libopenssl.SSL_accept_connection(ssl, flags)

def SSL_set_default_stream_mode(ssl: SSL, mode: int) -> int:
  return libopenssl.SSL_set_default_stream_mode(ssl, mode)

def SSL_set_incoming_stream_policy(ssl: SSL, policy: int, app_error_code: int):
  return libopenssl.SSL_set_incoming_stream_policy(ssl, policy, app_error_code)

def SSL_new_stream(ssl: SSL, flags: int) -> 'SSL | None':
  return libopenssl.SSL_new_stream(ssl, flags)

def SSL_accept_stream(ssl: SSL, flags: int) -> SSL:
  return libopenssl.SSL_accept_stream(ssl, flags)

def SSL_get_stream_type(ssl: SSL) -> int:
  return libopenssl.SSL_get_stream_type(ssl)

def SSL_get_stream_id(ssl: SSL) -> int:
  return libopenssl.SSL_get_stream_id(ssl)

def SSL_read(ssl: SSL, buf: c_void_p, num: int) -> int:
  return libopenssl.SSL_read(ssl, buf, num)

def SSL_write(ssl: SSL, buf: c_void_p, num: int) -> int:
  return libopenssl.SSL_write(ssl, buf, num)

def SSL_stream_conclude(ssl: SSL, flags: int) -> int:
  return libopenssl.SSL_stream_conclude(ssl, flags)

def SSL_stream_reset(ssl: SSL, args: 'SSL_STREAM_RESET_ARGS | None') -> int:
  if args is not None:
    return libopenssl.SSL_stream_reset(ssl, pointer(args) if args is not None else None, sizeof(SSL_STREAM_RESET_ARGS))
  else:
    return libopenssl.SSL_stream_reset(ssl, None, 0)

def SSL_get_error(ssl: SSL, ret: int) -> int:
  return libopenssl.SSL_get_error(ssl, ret)

def SSL_shutdown(ssl: SSL) -> int:
  return libopenssl.SSL_shutdown(ssl)

def SSL_shutdown_ex(ssl: SSL, flags: int, args: SSL_SHUTDOWN_EX_ARGS) -> int:
  return libopenssl.SSL_shutdown_ex(ssl, flags, pointer(args) if args is not None else None, sizeof(SSL_SHUTDOWN_EX_ARGS))

def ERR_clear_error() -> None:
  libcrypto.ERR_clear_error()

def ERR_peek_error() -> int:
  return libcrypto.ERR_peek_error()

def ERR_peek_last_error() -> int:
  return libcrypto.ERR_peek_last_error()

def ERR_GET_REASON(errcode: int):
  ERR_SYSTEM_FLAG = 2147483648
  ERR_SYSTEM_MASK = 2147483647
  ERR_REASON_MASK = 0x7FFFFF
  return (errcode & ERR_SYSTEM_MASK) if (errcode & ERR_SYSTEM_FLAG) != 0 else (errcode & ERR_REASON_MASK)

def ERR_print_errors(bio: BIO) -> None:
  libcrypto.ERR_print_errors(bio)

def BIO_meth_new(type: int, name: str) -> BIO_METHOD:
  return libcrypto.BIO_meth_new(type, name.encode())

def BIO_meth_free(biom: BIO_METHOD) -> None:
  libcrypto.BIO_meth_free(biom)

def BIO_meth_set_write(biom: BIO_METHOD, cb) -> int:
  return libcrypto.BIO_meth_set_write(biom, cb)

def BIO_new(biom: BIO_METHOD) -> BIO:
  return libcrypto.BIO_new(biom)

def BIO_free_all(bio: BIO) -> None:
  libcrypto.BIO_free_all(bio)

def BIO_get_data(bio: BIO) -> c_void_p:
  return libcrypto.BIO_get_data(bio)

def BIO_set_data(bio: BIO, ptr: c_void_p) -> None:
  libcrypto.BIO_set_data(bio, ptr)

def BIO_set_init(bio: BIO, init: int) -> None:
  libcrypto.BIO_set_init(bio, init)

def BIO_socket_nbio(sock: int, mode: int) -> int:
  return libcrypto.BIO_socket_nbio(sock, mode)

def BIO_closesocket(sock: int) -> int:
  return libcrypto.BIO_closesocket(sock)



def server_openssl_select_alpn_h3(ssl: SSL, out: c_void_p, out_len: c_void_p, in_, in_len: int, arg: c_void_p) -> int:
  OPENSSL_NPN_NEGOTIATED = 1
  SSL_TLSEXT_ERR_OK = 0
  SSL_TLSEXT_ERR_ALERT_FATAL = 2

  try:
    alpn = (c_ubyte * 3).from_buffer_copy(b'\x02h3')
    if SSL_select_next_proto(out, out_len, cast(alpn, POINTER(c_byte)), 3, in_, in_len) == OPENSSL_NPN_NEGOTIATED:
      return SSL_TLSEXT_ERR_OK

  except Exception:
    pass # raise through C functions isn't good idea

  return SSL_TLSEXT_ERR_ALERT_FATAL



def server_ctx_msg_debuglog(write_p: int, version: int, content_type: int, buf: c_void_p, len_: int, _: SSL, arg: c_void_p) -> None:
  try:
    f = cast(arg, POINTER(py_object)).contents.value # c-pointer to python IO object
    data = string_at(buf, len_)

    if content_type == 20:
      typ = b',CHANGE_CIPHER_SPEC'
    elif content_type == 21:
      typ = b',ALERT'
    elif content_type == 22:
      typ = b',HANDSHAKE'
    elif content_type == 0x100:
      typ = b',HEADER'
    elif content_type == 0x101:
      typ = b',INNER_CONTENT_TYPE'
    elif content_type == 0x200:
      typ = b',QUIC_DATAGRAM'
    elif content_type == 0x201:
      typ = b',QUIC_PACKET'
    elif content_type == 0x202:
      typ = b',QUIC_FRAME_FULL'
    elif content_type == 0x203:
      typ = b',QUIC_FRAME_HEADER'
    elif content_type == 0x204:
      typ = b',QUIC_FRAME_PADDING'
    else:
      typ = b''

    f.write(b''.join([
      b'SND(' if write_p else b'RCV(', str(version).encode('ascii'), typ, b',',
      str(len(data)).encode('ascii'), b'): ', data.hex().encode('ascii'), b'\r\n'
    ]))

  except Exception:
    pass



#
# all in one namespace
#
class OpenSSL:
  SSL_OP_TLSEXT_PADDING = 1 << 4
  SSL_OP_SAFARI_ECDHE_ECDSA_BUG = 1 << 6
  SSL_OP_IGNORE_UNEXPECTED_EOF = 1 << 7
  SSL_OP_DONT_INSERT_EMPTY_FRAGMENTS = 1 << 11
  SSL_OP_CRYPTOPRO_TLSEXT_BUG = 1 << 31
  SSL_OP_ALL = SSL_OP_CRYPTOPRO_TLSEXT_BUG | SSL_OP_DONT_INSERT_EMPTY_FRAGMENTS | SSL_OP_TLSEXT_PADDING | SSL_OP_SAFARI_ECDHE_ECDSA_BUG

  SSL_FILETYPE_PEM = 1
  SSL_FILETYPE_ASN1 = 2

  SSL_VERIFY_NONE = 0x0
  SSL_VERIFY_PEER = 0x1
  SSL_VERIFY_FAIL_IF_NO_PEER_CERT = 0x2
  SSL_VERIFY_CLIENT_ONCE = 0x4
  SSL_VERIFY_POST_HANDSHAKE = 0x8

  SSL_ACCEPT_STREAM_NO_BLOCK = 1

  SSL_DEFAULT_STREAM_MODE_NONE = 0
  SSL_DEFAULT_STREAM_MODE_AUTO_BIDI = 1
  SSL_DEFAULT_STREAM_MODE_AUTO_UNI = 2

  SSL_INCOMING_STREAM_POLICY_AUTO = 0
  SSL_INCOMING_STREAM_POLICY_ACCEPT = 1
  SSL_INCOMING_STREAM_POLICY_REJECT = 2

  SSL_STREAM_FLAG_UNI = 0x1
  SSL_STREAM_FLAG_NO_BLOCK = 0x2
  SSL_STREAM_FLAG_ADVANCE = 0x4

  SSL_STREAM_TYPE_NONE = 0
  SSL_STREAM_TYPE_READ = 1
  SSL_STREAM_TYPE_WRITE = 2
  SSL_STREAM_TYPE_BIDI = 3

  OPENSSL_NPN_UNSUPPORTED = 0
  OPENSSL_NPN_NEGOTIATED = 1
  OPENSSL_NPN_NO_OVERLAP = 2

  STREAM_RESET_ARGS = SSL_STREAM_RESET_ARGS
  SSL_SHUTDOWN_EX_ARGS = SSL_SHUTDOWN_EX_ARGS



  class Disconnected(Exception):
    pass


  class CTX:
    @staticmethod
    def new_quic_server():
      p = SSL_CTX_new(OSSL_QUIC_server_method())
      if p is None:
        errno = get_errno()
        raise RuntimeError("Failed to create OpenSSL context, errno=" + os.strerror(errno))

      return OpenSSL.CTX(p)

    def __init__(self, p: SSL_CTX = None):
      self.p = p

      # HTTP/3 + QUIC: ALPN must be negotiated in callback by set to 'h3' from:
      self.select_alpn_proc = ssl_ctx_alpn_select_proc(server_openssl_select_alpn_h3)
      SSL_CTX_set_alpn_select_cb(self.p, self.select_alpn_proc, None)

      # debugging:
      self._debug_msg_proc = ssl_ctx_msg_proc(server_ctx_msg_debuglog)
      self._debug_logfile = None
      self._debug_fptr: 'c_void_p | None' = None


    def free(self):
      SSL_CTX_free(self.p)

      if self._debug_logfile is not None:
        self._debug_logfile.close()
        self._debug_logfile = None
        self._debug_fptr = None


    def open_debug_logfile(self, filename: str):
      self._debug_logfile = open(filename, 'wb')
      try:
        self._debug_fptr = cast(pointer(py_object(self._debug_logfile)), c_void_p)
        SSL_CTX_set_msg_callback(self.p, self._debug_msg_proc)
        SSL_CTX_set_msg_callback_arg(self.p, self._debug_fptr)

      except Exception:
        self._debug_logfile.close()
        self._debug_logfile = None
        raise


    def use_certificate_chain_file(self, file: str):
      ERR_clear_error()
      if SSL_CTX_use_certificate_chain_file(self.p, file) <= 0:
        OpenSSL.raise_error("SSL_CTX_use_certificate_chain_file() failed, file: " + file)

    def use_private_key_file(self, file: str, type: int):
      ERR_clear_error()
      if SSL_CTX_use_PrivateKey_file(self.p, file, type) <= 0:
        OpenSSL.raise_error("SSL_CTX_use_PrivateKey_file() failed, file: " + file)


    def set_verify(self, mode: int, arg: c_void_p):
      SSL_CTX_set_verify(self.p, mode, arg)

    def set_options(self, options: int):
      SSL_CTX_set_options(self.p, options)


    def new_listener(self, flags: int):
      ERR_clear_error()
      ssl = SSL_new_listener(self.p, flags)
      if ssl is None:
        OpenSSL.raise_error("SSL_new_listener() failed")

      return OpenSSL.SSLListener(ssl)



  #
  # SSL* instance
  #
  class SSL:
    def __init__(self, p: SSL = None):
      self.p = p

    def free(self):
      SSL_free(self.p)



  #
  # SSL* as QUIC listener instance
  #
  class SSLListener(SSL):
    def set_fd(self, fd):
      ERR_clear_error()
      if SSL_set_fd(self.p, fd) == 0:
        OpenSSL.raise_error("SSL_set_fd() failed")

    def listen(self):
      ERR_clear_error()
      if SSL_listen(self.p) == 0:
        OpenSSL.raise_error("SSL_listen() failed")

    def accept_connection(self, flags: int = 0):
      ERR_clear_error()
      ssl = SSL_accept_connection(self.p, flags)
      return OpenSSL.SSLConn(ssl) if ssl is not None else None



  #
  # SSL* as QUIC connection instance
  #
  # instance lifetime:
  #  SSL_accept_connection() -> ... -> SSL_shutdown() or SSL_shutdown_ex() -> SSL_free()
  #
  class SSLConn(SSL):
    def shutdown(self):
      return SSL_shutdown(self.p) >= 0 # 0 or 1 is not an error state

    def shutdown_ex(self, flags: int = 0, args: 'SSL_SHUTDOWN_EX_ARGS | None' = None):
      return SSL_shutdown_ex(self.p, flags, args)


    def set_default_stream_mode(self, mode: int):
      ERR_clear_error()
      if SSL_set_default_stream_mode(self.p, mode) == 0:
        OpenSSL.raise_error("SSL_set_default_stream_mode() failed")

    def set_incoming_stream_policy(self, policy: int, app_error_code: int):
      ERR_clear_error()
      if SSL_set_incoming_stream_policy(self.p, policy, app_error_code) == 0:
        OpenSSL.raise_error("SSL_set_incoming_stream_policy() failed")


    def new_stream(self, flags: int = 0):
      ERR_clear_error()
      ssl = SSL_new_stream(self.p, flags)
      if ssl is None:
        OpenSSL.raise_error("SSL_new_stream() failed")

      return OpenSSL.SSLStream(ssl)


    def accept_stream(self, flags: int = 0):
      ERR_clear_error()
      ssl = SSL_accept_stream(self.p, flags)
      return OpenSSL.SSLStream(ssl) if ssl is not None else None



  #
  # SSL* as QUIC stream instance
  #
  # instance lifetime:
  #  SSL_accept_stream() or SSL_new_stream() -> ... -> SSL_stream_conclude() or SSL_stream_reset() -> SSL_free()
  #
  class SSLStream(SSL):
    def get_stream_type(self):
      return SSL_get_stream_type(self.p)

    def get_stream_id(self):
      return SSL_get_stream_id(self.p)


    def read(self, max_size: int) -> 'bytes | None':
      buf = create_string_buffer(max_size)
      ERR_clear_error()

      sz = SSL_read(self.p, buf, max_size)
      if sz > 0:
        return buf.raw[:sz]

      err = SSL_get_error(self.p, sz)
      if err == OpenSSL.SSL_ERROR_ZERO_RETURN:
        return None # no further reading available

      SSL_R_PROTOCOL_IS_SHUTDOWN = 207
      err_reason = ERR_GET_REASON(ERR_peek_error())

      if err_reason == SSL_R_PROTOCOL_IS_SHUTDOWN: # if client disconnected
        raise OpenSSL.Disconnected()

      OpenSSL.raise_error("SSL_read() failed: " + OpenSSL.ssl_error_str(err))


    def write(self, buf: bytes) -> None:
      offset = 0
      while offset < len(buf):
        ERR_clear_error()
        dat = buf[offset:] if offset != 0 else buf
        sz = SSL_write(self.p, dat, len(dat))

        if sz > 0:
          offset += sz
        else:
          err = SSL_get_error(self.p, sz)
          OpenSSL.raise_error("SSL_write() failed: " + OpenSSL.ssl_error_str(err))


    def stream_conclude(self, flags: int) -> int:
      return SSL_stream_conclude(self.p, flags)

    def stream_reset(self, args: 'SSL_STREAM_RESET_ARGS | None' = None):
      return SSL_stream_reset(self.p, args)



  #
  # set BIO no blocking mode
  #
  @staticmethod
  def socket_nbio(fd: int, mode: int):
    return BIO_socket_nbio(fd, mode)

  #
  # close/closesocket() OpenSSL wrapper
  #
  @staticmethod
  def closesocket(fd: int) -> int:
    return BIO_closesocket(fd)



  #
  # raise error with OpenSLL error stack formating
  #  if something goes wrong, just raise message only
  #
  @staticmethod
  def raise_error(message: str):
    biom = BIO_meth_new(0x0400, "error message bio") # type=TYPE_SOURCE_SINK
    if biom is None:
      raise RuntimeError(message)

    opensslmsg: 'str | None' = None
    try:
      proc = error_bio_write_proc(OpenSSL.raise_error_bio_write)
      if BIO_meth_set_write(biom, proc) <= 0:
        raise RuntimeError(message)

      bio = BIO_new(biom)
      if bio is None:
        raise RuntimeError(message)

      try:
        writes: 'list[bytes]' = list()
        arg = cast(pointer(py_object(writes)), c_void_p)
        BIO_set_data(bio, arg) # save python list object as c-pointer
        BIO_set_init(bio, 1)
        ERR_print_errors(bio)
        opensslmsg = bytes().join(writes).decode("utf-8", "replace")

      finally:
        BIO_free_all(bio)

    finally:
      BIO_meth_free(biom)

    raise RuntimeError(message + (("\n" + opensslmsg) if opensslmsg else ""))


  @staticmethod
  def raise_error_bio_write(bio: BIO, data, len: int) -> int:
    writes: 'list[bytes]' = cast(BIO_get_data(bio), POINTER(py_object)).contents.value # c-pointer to python list object
    writes.append(string_at(data, len))
    return len



  # "The TLS/SSL I/O operation completed. This result code is returned if and only if ret > 0."
  SSL_ERROR_NONE = 0

  # "A non-recoverable, fatal error in the SSL library occurred, usually a protocol error.
  #  The OpenSSL error queue contains more information on the error. If this error occurs
  #  then no further I/O operations should be performed on the connection and SSL_shutdown()
  #  must not be called."
  SSL_ERROR_SSL = 1

  SSL_ERROR_WANT_READ = 2
  SSL_ERROR_WANT_WRITE = 3
  SSL_ERROR_WANT_X509_LOOKUP = 4

  # "Some non-recoverable, fatal I/O error occurred. The OpenSSL error queue may contain more information on the error.
  #  For socket I/O on Unix systems, consult errno for details. If this error occurs then no further I/O operations
  #  should be performed on the connection and SSL_shutdown() must not be called."
  SSL_ERROR_SYSCALL = 5

  # "The TLS/SSL peer has closed the connection for writing by sending the close_notify alert.
  #  No more data can be read. Note that SSL_ERROR_ZERO_RETURN does not necessarily indicate that the underlying transport has been closed.""
  SSL_ERROR_ZERO_RETURN = 6

  SSL_ERROR_WANT_CONNECT = 7
  SSL_ERROR_WANT_ACCEPT = 8
  SSL_ERROR_WANT_ASYNC = 9
  SSL_ERROR_WANT_ASYNC_JOB = 10
  SSL_ERROR_WANT_CLIENT_HELLO_CB = 11
  SSL_ERROR_WANT_RETRY_VERIFY = 12


  @staticmethod
  def ssl_error_str(err: int):
    if err == OpenSSL.SSL_ERROR_NONE:
      return "SSL_ERROR_NONE"
    if err == OpenSSL.SSL_ERROR_SSL:
      return "SSL_ERROR_SSL"
    if err == OpenSSL.SSL_ERROR_WANT_READ:
      return "SSL_ERROR_WANT_READ"
    if err == OpenSSL.SSL_ERROR_WANT_WRITE:
      return "SSL_ERROR_WANT_WRITE"
    if err == OpenSSL.SSL_ERROR_WANT_X509_LOOKUP:
      return "SSL_ERROR_WANT_X509_LOOKUP"
    if err == OpenSSL.SSL_ERROR_SYSCALL:
      return "SSL_ERROR_SYSCALL"
    if err == OpenSSL.SSL_ERROR_ZERO_RETURN:
      return "SSL_ERROR_ZERO_RETURN"
    if err == OpenSSL.SSL_ERROR_WANT_CONNECT:
      return "SSL_ERROR_WANT_CONNECT"
    if err == OpenSSL.SSL_ERROR_WANT_ACCEPT:
      return "SSL_ERROR_WANT_ACCEPT"
    if err == OpenSSL.SSL_ERROR_WANT_ASYNC:
      return "SSL_ERROR_WANT_ASYNC"
    if err == OpenSSL.SSL_ERROR_WANT_ASYNC_JOB:
      return "SSL_ERROR_WANT_ASYNC_JOB"
    if err == OpenSSL.SSL_ERROR_WANT_CLIENT_HELLO_CB:
      return "SSL_ERROR_WANT_CLIENT_HELLO_CB"
    if err == OpenSSL.SSL_ERROR_WANT_RETRY_VERIFY:
      return "SSL_ERROR_WANT_RETRY_VERIFY"

    return "SSL_ERROR_UNKNOWN"

## PyHTTPServer
Implementation of HTTP server in pure (almost) python.
Usefull when you need custom backed for your application or web page.
All what you need to do is attach processing handlers (see example).

### Features
- Managing connections.
- Support HTTP1, HTTP/2 and HTTP/3 requests.
- Support SSL/TLS (need cert file).
- Support implmeneting WebSocket messaging.

### Usage examples
- <a href="blob/main/example/basic_http_server.py">Basic HTTP Server</a>

### HTTP/3 Notes
To support HTTP/3 OpenSSL (min ver 3.5) binaries are needed.
This version not implement QUIC protocol in pure python yet.
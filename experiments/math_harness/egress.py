"""HTTPS CONNECT proxy for workers on isolated, internal Docker networks."""

from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
import select
import socket

ALLOWED_HOSTS = frozenset({"chatgpt.com", "auth.openai.com", "api.openai.com"})


def destination(authority):
    host, separator, port = authority.rpartition(":")
    if separator != ":" or port != "443" or host.lower() not in ALLOWED_HOSTS:
        raise ValueError("Only the configured model/authentication hosts on port 443 are allowed")
    return host.lower(), 443


class ProxyHandler(BaseHTTPRequestHandler):
    def log_message(self, *_):
        pass

    def do_CONNECT(self):
        try:
            target = destination(self.path)
        except ValueError:
            self.send_error(403, "Destination is not allowed")
            return
        try:
            upstream = socket.create_connection(target, timeout=20)
        except OSError:
            self.send_error(502, "Model endpoint connection failed")
            return
        with upstream:
            self.send_response(200, "Connection Established")
            self.end_headers()
            self.wfile.flush()
            self.close_connection = True
            connections = (self.connection, upstream)
            for connection in connections:
                connection.settimeout(30)
            try:
                while not self.server.stopping.is_set():
                    readable, _, _ = select.select(connections, [], [], 1)
                    for incoming in readable:
                        data = incoming.recv(65536)
                        if not data:
                            return
                        outgoing = upstream if incoming is self.connection else self.connection
                        outgoing.sendall(data)
            except (OSError, ValueError):
                return

    def do_GET(self):
        self.send_error(403, "Only HTTPS CONNECT is supported")


def proxy(address):
    import threading
    server = ThreadingHTTPServer((address, 0), ProxyHandler)
    server.stopping = threading.Event()
    return server

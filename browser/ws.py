"""A websocket client small enough to read in one sitting.

Chrome DevTools needs a websocket for everything except a handful of HTTP
endpoints, and this repository installs nothing, so this is RFC 6455 cut down to
what one local, trusted, text-only connection needs: no TLS, no extensions, no
compression, no server role.
"""

import base64
import hashlib
import os
import socket
import struct
from urllib.parse import urlparse

GUID = "258EAFA5-E914-47DA-95CA-C5AB0DC85B11"

CONTINUATION, TEXT, BINARY, CLOSE, PING, PONG = 0x0, 0x1, 0x2, 0x8, 0x9, 0xA


class WebSocketError(Exception):
    pass


class Timeout(WebSocketError):
    pass


class WebSocket:
    def __init__(self, url, timeout=20.0):
        parts = urlparse(url)
        if parts.scheme != "ws":
            raise WebSocketError("only ws:// is supported, not %r" % url)
        self._socket = socket.create_connection((parts.hostname, parts.port or 80), timeout)
        self._socket.settimeout(timeout)
        self._buffer = b""
        self._closed = False
        self._handshake(parts)

    # -- setting up --------------------------------------------------------

    def _handshake(self, parts):
        key = base64.b64encode(os.urandom(16)).decode()
        path = parts.path or "/"
        if parts.query:
            path += "?" + parts.query
        self._socket.sendall(
            (
                "GET %s HTTP/1.1\r\n"
                "Host: %s:%d\r\n"
                "Upgrade: websocket\r\n"
                "Connection: Upgrade\r\n"
                "Sec-WebSocket-Key: %s\r\n"
                "Sec-WebSocket-Version: 13\r\n"
                "\r\n" % (path, parts.hostname, parts.port or 80, key)
            ).encode()
        )
        head = self._read_until(b"\r\n\r\n").decode("latin1")
        if " 101" not in head.split("\r\n", 1)[0]:
            raise WebSocketError("the server refused to upgrade: %s" % head.split("\r\n", 1)[0])
        expected = base64.b64encode(hashlib.sha1((key + GUID).encode()).digest()).decode()
        for line in head.split("\r\n"):
            if line.lower().startswith("sec-websocket-accept:"):
                if line.split(":", 1)[1].strip() != expected:
                    raise WebSocketError("the server's accept key does not match")
                return
        raise WebSocketError("the server sent no accept key")

    # -- reading and writing bytes -----------------------------------------

    def _read_until(self, marker):
        while marker not in self._buffer:
            self._fill()
        head, _, self._buffer = self._buffer.partition(marker)
        return head + marker

    def _fill(self):
        try:
            chunk = self._socket.recv(65536)
        except socket.timeout:
            raise Timeout("the browser sent nothing back in time")
        if not chunk:
            raise WebSocketError("the browser closed the connection")
        self._buffer += chunk

    def _read(self, count):
        while len(self._buffer) < count:
            self._fill()
        out, self._buffer = self._buffer[:count], self._buffer[count:]
        return out

    # -- frames ------------------------------------------------------------

    def _send_frame(self, opcode, payload):
        if self._closed:
            raise WebSocketError("the connection is closed")
        header = bytearray([0x80 | opcode])
        size = len(payload)
        if size < 126:
            header.append(0x80 | size)
        elif size < 1 << 16:
            header.append(0x80 | 126)
            header += struct.pack(">H", size)
        else:
            header.append(0x80 | 127)
            header += struct.pack(">Q", size)
        mask = os.urandom(4)
        header += mask
        self._socket.sendall(bytes(header) + bytes(payload[i] ^ mask[i & 3] for i in range(size)))

    def _read_frame(self):
        head = self._read(2)
        final = bool(head[0] & 0x80)
        opcode = head[0] & 0x0F
        masked = bool(head[1] & 0x80)
        size = head[1] & 0x7F
        if size == 126:
            size = struct.unpack(">H", self._read(2))[0]
        elif size == 127:
            size = struct.unpack(">Q", self._read(8))[0]
        mask = self._read(4) if masked else b""
        payload = self._read(size)
        if masked:
            payload = bytes(payload[i] ^ mask[i & 3] for i in range(size))
        return final, opcode, payload

    # -- messages ----------------------------------------------------------

    def send(self, text):
        self._send_frame(TEXT, text.encode("utf-8"))

    def recv(self):
        """The next whole text message. Pings are answered, control frames skipped."""
        parts = []
        while True:
            final, opcode, payload = self._read_frame()
            if opcode == PING:
                self._send_frame(PONG, payload)
                continue
            if opcode == PONG:
                continue
            if opcode == CLOSE:
                self._closed = True
                raise WebSocketError("the browser closed the connection")
            if opcode == BINARY:
                raise WebSocketError("a binary frame arrived; DevTools should only send text")
            parts.append(payload)
            if final:
                return b"".join(parts).decode("utf-8")

    def settimeout(self, seconds):
        self._socket.settimeout(seconds)

    def close(self):
        if not self._closed:
            try:
                self._send_frame(CLOSE, b"")
            except (OSError, WebSocketError):
                pass
            self._closed = True
        self._socket.close()

    def __enter__(self):
        return self

    def __exit__(self, *exc):
        self.close()

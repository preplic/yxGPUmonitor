#!/usr/bin/python3
"""SSH ProxyCommand using the selected HTTP proxy; never fall back to direct."""

import base64
import os
import select
import socket
import ssl
import sys
from urllib.parse import unquote, urlsplit


def main():
    host, port = sys.argv[1:]
    proxy = next((os.environ[name] for name in ("https_proxy", "HTTPS_PROXY", "http_proxy", "HTTP_PROXY")
                  if os.environ.get(name)), None)
    if not proxy:
        raise ValueError("No selected HTTP proxy is configured")
    parsed = urlsplit(proxy)
    if parsed.scheme not in ("http", "https") or not parsed.hostname:
        raise ValueError("SSH publisher requires an HTTP CONNECT proxy")
    connection = socket.create_connection((parsed.hostname, parsed.port or (443 if parsed.scheme == "https" else 80)), timeout=20)
    if parsed.scheme == "https":
        connection = ssl.create_default_context().wrap_socket(connection, server_hostname=parsed.hostname)
    headers = ["CONNECT %s:%s HTTP/1.1" % (host, port), "Host: %s:%s" % (host, port)]
    if parsed.username is not None:
        auth = "%s:%s" % (unquote(parsed.username), unquote(parsed.password or ""))
        headers.append("Proxy-Authorization: Basic " + base64.b64encode(auth.encode()).decode())
    connection.sendall(("\r\n".join(headers) + "\r\n\r\n").encode())
    response = b""
    while not response.endswith(b"\r\n\r\n"):
        part = connection.recv(1)
        if not part or len(response) > 65536:
            raise ValueError("Incomplete HTTP CONNECT response")
        response += part
    if response.split(b"\r\n", 1)[0].split()[1] != b"200":
        raise ValueError("Selected proxy rejected the SSH tunnel")
    connection.settimeout(None)
    readers = [connection, sys.stdin.buffer]
    while True:
        ready, _, _ = select.select(readers, [], [])
        if connection in ready:
            data = connection.recv(65536)
            if not data:
                return
            sys.stdout.buffer.write(data)
            sys.stdout.buffer.flush()
        if sys.stdin.buffer in ready:
            data = os.read(sys.stdin.fileno(), 65536)
            if not data:
                readers.remove(sys.stdin.buffer)
                connection.shutdown(socket.SHUT_WR)
            else:
                connection.sendall(data)


if __name__ == "__main__":
    try:
        main()
    except (OSError, ValueError):
        sys.exit("Selected proxy tunnel failed; no direct connection attempted.")

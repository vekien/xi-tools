"""Standalone WebSocket bridge server for xi-zone-editor.

Serves ``ws://127.0.0.1:<port>/ws`` and dispatches JSON-RPC style messages to
``xi.zone.xi_bridge.handle_command``. No static file server — the editor UI lives
in its own Tauri/Vite shell.

Idle exit: after the first client has ever connected, if no WebSocket clients
remain for ``--idle-secs`` the process exits (orphan guard). A ``bridge.ping``
method (or any traffic) resets activity.
"""

from __future__ import annotations

import base64
import hashlib
import json
import select
import socket
import struct
import sys
import threading
import time
import traceback
from typing import Callable

import click

DEFAULT_HOST = "127.0.0.1"
DEFAULT_PORT = 8777
DEFAULT_IDLE_SECS = 90


# ── minimal RFC6455 server (text frames only) ────────────────────────────────

def _ws_accept_key(key: str) -> str:
    guid = "258EAFA5-E914-47DA-95CA-C5AB0DC85B11"
    dig = hashlib.sha1((key + guid).encode("utf-8")).digest()
    return base64.b64encode(dig).decode("ascii")


def _recv_exact(conn: socket.socket, n: int) -> bytes | None:
    buf = b""
    while len(buf) < n:
        chunk = conn.recv(n - len(buf))
        if not chunk:
            return None
        buf += chunk
    return buf


def _read_http_headers(conn: socket.socket) -> tuple[str, dict[str, str]] | None:
    data = b""
    while b"\r\n\r\n" not in data:
        chunk = conn.recv(4096)
        if not chunk:
            return None
        data += chunk
        if len(data) > 65536:
            return None
    head, _ = data.split(b"\r\n\r\n", 1)
    lines = head.decode("iso-8859-1", errors="replace").split("\r\n")
    if not lines:
        return None
    req = lines[0]
    headers: dict[str, str] = {}
    for line in lines[1:]:
        if ":" in line:
            k, v = line.split(":", 1)
            headers[k.strip().lower()] = v.strip()
    return req, headers


def _ws_handshake(conn: socket.socket) -> bool:
    parsed = _read_http_headers(conn)
    if not parsed:
        return False
    req, headers = parsed
    if not req.startswith("GET ") or headers.get("upgrade", "").lower() != "websocket":
        conn.sendall(b"HTTP/1.1 400 Bad Request\r\nConnection: close\r\n\r\n")
        return False
    key = headers.get("sec-websocket-key")
    if not key:
        conn.sendall(b"HTTP/1.1 400 Bad Request\r\nConnection: close\r\n\r\n")
        return False
    path = req.split(" ")[1] if " " in req else "/"
    if not path.startswith("/ws"):
        conn.sendall(b"HTTP/1.1 404 Not Found\r\nConnection: close\r\n\r\n")
        return False
    accept = _ws_accept_key(key)
    resp = (
        "HTTP/1.1 101 Switching Protocols\r\n"
        "Upgrade: websocket\r\n"
        "Connection: Upgrade\r\n"
        f"Sec-WebSocket-Accept: {accept}\r\n"
        "\r\n"
    )
    conn.sendall(resp.encode("ascii"))
    return True


def _ws_recv_text(conn: socket.socket) -> str | None:
    """Return decoded text frame, '' for ping handled, None on close/error."""
    hdr = _recv_exact(conn, 2)
    if not hdr:
        return None
    b0, b1 = hdr[0], hdr[1]
    opcode = b0 & 0x0F
    masked = bool(b1 & 0x80)
    length = b1 & 0x7F
    if length == 126:
        ext = _recv_exact(conn, 2)
        if not ext:
            return None
        length = struct.unpack("!H", ext)[0]
    elif length == 127:
        ext = _recv_exact(conn, 8)
        if not ext:
            return None
        length = struct.unpack("!Q", ext)[0]
    mask = _recv_exact(conn, 4) if masked else b"\x00\x00\x00\x00"
    if mask is None:
        return None
    payload = _recv_exact(conn, length) if length else b""
    if payload is None:
        return None
    if masked:
        payload = bytes(b ^ mask[i % 4] for i, b in enumerate(payload))
    if opcode == 0x8:  # close
        return None
    if opcode == 0x9:  # ping → pong
        _ws_send_frame(conn, payload, opcode=0xA)
        return ""
    if opcode == 0xA:  # pong
        return ""
    if opcode != 0x1:  # text only
        return ""
    try:
        return payload.decode("utf-8")
    except UnicodeDecodeError:
        return ""


def _ws_send_frame(conn: socket.socket, payload: bytes, opcode: int = 0x1) -> None:
    header = bytes([0x80 | (opcode & 0x0F)])
    n = len(payload)
    if n < 126:
        header += bytes([n])
    elif n < 65536:
        header += bytes([126]) + struct.pack("!H", n)
    else:
        header += bytes([127]) + struct.pack("!Q", n)
    conn.sendall(header + payload)


def _ws_send_text(conn: socket.socket, text: str) -> None:
    _ws_send_frame(conn, text.encode("utf-8"), opcode=0x1)


# ── bridge session ───────────────────────────────────────────────────────────

class BridgeServer:
    def __init__(self, host: str, port: int, idle_secs: float):
        self.host = host
        self.port = port
        self.idle_secs = idle_secs
        self._sock: socket.socket | None = None
        self._clients = 0
        self._clients_lock = threading.Lock()
        self._last_activity = time.monotonic()
        self._ever_client = False
        self._stop = threading.Event()

    def touch(self) -> None:
        self._last_activity = time.monotonic()

    def run(self) -> int:
        from xi.zone.xi_bridge import handle_command

        srv = socket.socket(socket.AF_INET, socket.SOCK_STREAM)
        srv.setsockopt(socket.SOL_SOCKET, socket.SO_REUSEADDR, 1)
        srv.bind((self.host, self.port))
        srv.listen(16)
        srv.settimeout(1.0)
        self._sock = srv
        click.echo(f"xi bridge listening on ws://{self.host}:{self.port}/ws "
                   f"(idle exit {self.idle_secs:g}s after last client)", err=True)

        idle_thread = threading.Thread(target=self._idle_watch, daemon=True)
        idle_thread.start()

        try:
            while not self._stop.is_set():
                try:
                    conn, addr = srv.accept()
                except socket.timeout:
                    continue
                except OSError:
                    break
                threading.Thread(
                    target=self._client_loop,
                    args=(conn, addr, handle_command),
                    daemon=True,
                ).start()
        finally:
            try:
                srv.close()
            except OSError:
                pass
        return 0

    def _idle_watch(self) -> None:
        while not self._stop.is_set():
            time.sleep(2.0)
            if not self._ever_client:
                continue
            with self._clients_lock:
                n = self._clients
            if n > 0:
                continue
            idle = time.monotonic() - self._last_activity
            if idle >= self.idle_secs:
                click.echo(f"xi bridge: no clients for {self.idle_secs:g}s — exiting", err=True)
                self._stop.set()
                try:
                    if self._sock:
                        self._sock.close()
                except OSError:
                    pass
                return

    def _client_loop(self, conn: socket.socket, addr, handle_command: Callable) -> None:
        conn.settimeout(120.0)
        try:
            if not _ws_handshake(conn):
                return
            with self._clients_lock:
                self._clients += 1
                self._ever_client = True
            self.touch()
            click.echo(f"bridge client connected from {addr[0]}:{addr[1]}", err=True)

            while not self._stop.is_set():
                try:
                    text = _ws_recv_text(conn)
                except socket.timeout:
                    continue
                except OSError:
                    break
                if text is None:
                    break
                if text == "":
                    self.touch()
                    continue
                self.touch()
                self._handle_message(conn, text, handle_command)
        finally:
            with self._clients_lock:
                self._clients = max(0, self._clients - 1)
            self.touch()
            try:
                conn.close()
            except OSError:
                pass
            click.echo(f"bridge client disconnected {addr[0]}:{addr[1]}", err=True)

    def _handle_message(self, conn: socket.socket, text: str, handle_command: Callable) -> None:
        try:
            msg = json.loads(text)
        except json.JSONDecodeError:
            return
        req_id = msg.get("id")
        method = msg.get("method") or ""
        params = msg.get("params") or {}

        # Control: cancel is handled inside handle_command if supported.
        if method == "bridge.ping":
            _ws_send_text(conn, json.dumps({"id": req_id, "ok": True, "result": {"pong": True, "ts": time.time()}}))
            return

        def log_line(line: str) -> None:
            try:
                _ws_send_text(conn, json.dumps({"id": req_id, "type": "log", "line": line}))
                self.touch()
            except OSError:
                pass

        # Capture print-style progress if the handler uses a logger callback via params.
        # Most handlers don't; streamed logs go through explicit log hooks in handle_command.
        try:
            # Thread-local stdout tee for long ops that print.
            result = self._run_with_log_tee(handle_command, method, params, log_line)
            out = {"id": req_id, "ok": True, "result": result}
        except Exception as exc:  # noqa: BLE001
            err = str(exc) or exc.__class__.__name__
            if not isinstance(exc, (ValueError, RuntimeError, FileNotFoundError, click.ClickException)):
                traceback.print_exc(file=sys.stderr)
            out = {"id": req_id, "ok": False, "error": err}
        try:
            _ws_send_text(conn, json.dumps(out, default=str))
        except OSError:
            pass

    def _run_with_log_tee(self, handle_command, method, params, log_line):
        """Run handle_command; tee writes to stderr-like progress via log frames when possible."""
        class _Tee:
            def __init__(self, real):
                self._real = real
                self._buf = ""
            def write(self, s):
                if not s:
                    return 0
                self._real.write(s)
                self._buf += s
                while "\n" in self._buf:
                    line, self._buf = self._buf.split("\n", 1)
                    log_line(line + "\n")
                return len(s)
            def flush(self):
                self._real.flush()
                if self._buf:
                    log_line(self._buf)
                    self._buf = ""

        old_out, old_err = sys.stdout, sys.stderr
        sys.stdout = _Tee(old_out)  # type: ignore[assignment]
        sys.stderr = _Tee(old_err)  # type: ignore[assignment]
        try:
            return handle_command(method, params)
        finally:
            try:
                sys.stdout.flush()
                sys.stderr.flush()
            except Exception:
                pass
            sys.stdout, sys.stderr = old_out, old_err


@click.command("bridge")
@click.option("--host", default=DEFAULT_HOST, show_default=True)
@click.option("--port", default=DEFAULT_PORT, show_default=True, type=int)
@click.option("--idle-secs", default=DEFAULT_IDLE_SECS, show_default=True, type=float,
              help="Exit after this many seconds with zero connected clients "
                   "(once at least one client has connected). 0 = never.")
def cmd(host: str, port: int, idle_secs: float):
    """Run the zone-editor WebSocket bridge (no UI).

    Used by xi-zone-editor's Tauri shell. Connect at ws://HOST:PORT/ws.
    """
    # Allow bridge without FFXI_DIR for ping-only; real ops still need it.
    idle = idle_secs if idle_secs > 0 else 1e12
    srv = BridgeServer(host, port, idle)
    raise SystemExit(srv.run())

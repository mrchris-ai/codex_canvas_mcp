"""Bridge remote stdio MCP clients to a server running in the macOS GUI session."""

from __future__ import annotations

import os
from pathlib import Path
import socket
import stat
import subprocess
import sys
import threading
from typing import BinaryIO


BUFFER_SIZE = 64 * 1024
SOCKET_ENV = "CANVAS_MCP_SOCKET"
FORBIDDEN_CHILD_ENV = ("OP_SERVICE_ACCOUNT_TOKEN", "CANVAS_WRITE_POLICY")


class BridgeConfigurationError(RuntimeError):
    """Raised when the local bridge cannot enforce its security boundary."""


def configured_socket_path() -> Path:
    raw = os.environ.get(SOCKET_ENV, "").strip()
    if not raw:
        raise BridgeConfigurationError(f"{SOCKET_ENV} is required.")
    path = Path(raw)
    if not path.is_absolute():
        raise BridgeConfigurationError(f"{SOCKET_ENV} must be an absolute path.")
    return path


def require_private_parent(path: Path) -> None:
    parent = path.parent
    parent.mkdir(mode=0o700, parents=True, exist_ok=True)
    if parent.is_symlink():
        raise BridgeConfigurationError("Socket directory must not be a symlink.")
    metadata = parent.lstat()
    if not stat.S_ISDIR(metadata.st_mode):
        raise BridgeConfigurationError("Socket parent must be a directory.")
    if metadata.st_uid != os.getuid():
        raise BridgeConfigurationError("Socket directory must be owned by the current user.")
    if stat.S_IMODE(metadata.st_mode) & 0o077:
        raise BridgeConfigurationError("Socket directory permissions must be 0700 or stricter.")


def remove_stale_socket(path: Path) -> None:
    try:
        metadata = path.lstat()
    except FileNotFoundError:
        return
    if not stat.S_ISSOCK(metadata.st_mode) or metadata.st_uid != os.getuid():
        raise BridgeConfigurationError("Refusing to replace a non-socket or foreign socket path.")

    probe = socket.socket(socket.AF_UNIX, socket.SOCK_STREAM)
    try:
        probe.connect(str(path))
    except ConnectionRefusedError:
        path.unlink()
    except FileNotFoundError:
        return
    else:
        raise BridgeConfigurationError("Canvas MCP socket is already active.")
    finally:
        probe.close()


def prepare_listener(path: Path) -> socket.socket:
    require_private_parent(path)
    remove_stale_socket(path)
    listener = socket.socket(socket.AF_UNIX, socket.SOCK_STREAM)
    try:
        listener.bind(str(path))
        path.chmod(0o600)
        listener.listen()
    except Exception:
        listener.close()
        raise
    return listener


def proxy_stdio(path: Path, input_stream: BinaryIO, output_stream: BinaryIO) -> None:
    connection = socket.socket(socket.AF_UNIX, socket.SOCK_STREAM)
    connection.connect(str(path))
    sender_errors: list[Exception] = []

    def send_input() -> None:
        read_chunk = getattr(input_stream, "read1", input_stream.read)
        try:
            while chunk := read_chunk(BUFFER_SIZE):
                connection.sendall(chunk)
            try:
                connection.shutdown(socket.SHUT_WR)
            except OSError:
                pass
        except BrokenPipeError:
            return
        except Exception as error:
            sender_errors.append(error)
            try:
                connection.shutdown(socket.SHUT_RDWR)
            except OSError:
                pass

    sender = threading.Thread(target=send_input, daemon=True)
    sender.start()
    try:
        while chunk := connection.recv(BUFFER_SIZE):
            output_stream.write(chunk)
            output_stream.flush()
    finally:
        connection.close()
        sender.join(timeout=1)
    if sender_errors:
        raise sender_errors[0]


def sanitized_server_environment() -> dict[str, str]:
    environment = dict(os.environ)
    for name in FORBIDDEN_CHILD_ENV:
        environment.pop(name, None)
    return environment


def serve_connection(connection: socket.socket, command: list[str] | None = None) -> None:
    with connection:
        process = subprocess.Popen(
            command or [sys.executable, "-m", "canvas_mcp"],
            stdin=connection,
            stdout=connection,
            close_fds=True,
            env=sanitized_server_environment(),
        )
        process.wait()


def run_daemon(path: Path) -> None:
    listener = prepare_listener(path)
    try:
        while True:
            connection, _ = listener.accept()
            threading.Thread(
                target=serve_connection,
                args=(connection,),
                daemon=True,
            ).start()
    finally:
        listener.close()
        try:
            metadata = path.lstat()
        except FileNotFoundError:
            return
        if stat.S_ISSOCK(metadata.st_mode) and metadata.st_uid == os.getuid():
            path.unlink()


def main() -> None:
    if len(sys.argv) != 2 or sys.argv[1] not in {"client", "daemon"}:
        raise SystemExit("Usage: python -m canvas_mcp.socket_bridge [client|daemon]")
    path = configured_socket_path()
    if sys.argv[1] == "client":
        proxy_stdio(path, sys.stdin.buffer, sys.stdout.buffer)
    else:
        run_daemon(path)


if __name__ == "__main__":
    main()

import io
import json
import os
from pathlib import Path
import queue
import socket
import stat
import sys
import tempfile
import threading
import unittest
from unittest import mock

from canvas_mcp import socket_bridge


class SocketBridgeTests(unittest.TestCase):
    def test_socket_path_must_be_absolute(self):
        with mock.patch.dict(os.environ, {"CANVAS_MCP_SOCKET": "relative.sock"}, clear=True):
            with self.assertRaises(socket_bridge.BridgeConfigurationError):
                socket_bridge.configured_socket_path()

    def test_listener_requires_private_directory(self):
        with tempfile.TemporaryDirectory() as directory:
            parent = Path(directory) / "shared"
            parent.mkdir(mode=0o755)
            parent.chmod(0o755)
            with self.assertRaises(socket_bridge.BridgeConfigurationError):
                socket_bridge.prepare_listener(parent / "canvas.sock")

    def test_listener_replaces_owned_stale_socket_and_is_private(self):
        with tempfile.TemporaryDirectory() as directory:
            parent = Path(directory) / "private"
            parent.mkdir(mode=0o700)
            path = parent / "canvas.sock"
            stale = socket.socket(socket.AF_UNIX, socket.SOCK_STREAM)
            stale.bind(str(path))
            stale.close()

            listener = socket_bridge.prepare_listener(path)
            try:
                mode = stat.S_IMODE(path.stat().st_mode)
                self.assertEqual(mode, 0o600)
                self.assertTrue(stat.S_ISSOCK(path.stat().st_mode))
            finally:
                listener.close()

    def test_listener_refuses_to_replace_regular_file(self):
        with tempfile.TemporaryDirectory() as directory:
            parent = Path(directory) / "private"
            parent.mkdir(mode=0o700)
            path = parent / "canvas.sock"
            path.write_text("do not replace", encoding="utf-8")
            with self.assertRaises(socket_bridge.BridgeConfigurationError):
                socket_bridge.prepare_listener(path)
            self.assertEqual(path.read_text(encoding="utf-8"), "do not replace")

    def test_listener_refuses_to_replace_active_socket(self):
        with tempfile.TemporaryDirectory() as directory:
            parent = Path(directory) / "private"
            parent.mkdir(mode=0o700)
            path = parent / "canvas.sock"
            active = socket.socket(socket.AF_UNIX, socket.SOCK_STREAM)
            active.bind(str(path))
            active.listen()
            try:
                with self.assertRaisesRegex(
                    socket_bridge.BridgeConfigurationError,
                    "already active",
                ):
                    socket_bridge.prepare_listener(path)
            finally:
                active.close()
                path.unlink()

    def test_client_forwards_stdio_over_unix_socket(self):
        with tempfile.TemporaryDirectory() as directory:
            parent = Path(directory) / "private"
            parent.mkdir(mode=0o700)
            path = parent / "canvas.sock"
            listener = socket_bridge.prepare_listener(path)
            requests = []

            def serve_once():
                connection, _ = listener.accept()
                with connection:
                    requests.append(connection.recv(1024))
                    connection.sendall(b'{"jsonrpc":"2.0","result":{}}\n')

            server_thread = threading.Thread(target=serve_once)
            server_thread.start()
            output = io.BytesIO()
            try:
                socket_bridge.proxy_stdio(
                    path,
                    io.BytesIO(b'{"jsonrpc":"2.0"}\n'),
                    output,
                )
            finally:
                server_thread.join(timeout=2)
                listener.close()

            self.assertFalse(server_thread.is_alive())
            self.assertEqual(requests, [b'{"jsonrpc":"2.0"}\n'])
            self.assertEqual(output.getvalue(), b'{"jsonrpc":"2.0","result":{}}\n')

    def test_client_forwards_request_before_stdin_eof(self):
        with tempfile.TemporaryDirectory() as directory:
            parent = Path(directory) / "private"
            parent.mkdir(mode=0o700)
            path = parent / "canvas.sock"
            listener = socket_bridge.prepare_listener(path)
            received = queue.Queue()

            def serve_once():
                connection, _ = listener.accept()
                with connection:
                    request = connection.recv(1024)
                    received.put(request)
                    connection.sendall(b'{"jsonrpc":"2.0","result":{}}\n')

            server_thread = threading.Thread(target=serve_once)
            server_thread.start()
            read_fd, write_fd = os.pipe()
            input_stream = os.fdopen(read_fd, "rb")
            writer = os.fdopen(write_fd, "wb", buffering=0)
            output = io.BytesIO()
            client_thread = threading.Thread(
                target=socket_bridge.proxy_stdio,
                args=(path, input_stream, output),
            )
            client_thread.start()

            request = b'{"jsonrpc":"2.0","method":"initialize"}\n'
            try:
                writer.write(request)
                self.assertEqual(received.get(timeout=1), request)
            finally:
                writer.close()
                client_thread.join(timeout=2)
                server_thread.join(timeout=2)
                input_stream.close()
                listener.close()

            self.assertFalse(client_thread.is_alive())
            self.assertFalse(server_thread.is_alive())
            self.assertEqual(output.getvalue(), b'{"jsonrpc":"2.0","result":{}}\n')

    def test_client_propagates_input_failure(self):
        class FailingInput:
            def read(self, size):
                raise OSError("synthetic input failure")

        with tempfile.TemporaryDirectory() as directory:
            parent = Path(directory) / "private"
            parent.mkdir(mode=0o700)
            path = parent / "canvas.sock"
            listener = socket_bridge.prepare_listener(path)

            def serve_once():
                connection, _ = listener.accept()
                with connection:
                    connection.recv(1024)

            server_thread = threading.Thread(target=serve_once)
            server_thread.start()
            try:
                with self.assertRaisesRegex(OSError, "synthetic input failure"):
                    socket_bridge.proxy_stdio(path, FailingInput(), io.BytesIO())
            finally:
                server_thread.join(timeout=2)
                listener.close()

            self.assertFalse(server_thread.is_alive())

    def test_serve_connection_starts_child_with_sanitized_environment(self):
        server_socket, client_socket = socket.socketpair()
        errors = queue.Queue()
        script = (
            "import json, os, sys; "
            "json.dump({"
            "'service_token': 'OP_SERVICE_ACCOUNT_TOKEN' in os.environ, "
            "'write_policy': 'CANVAS_WRITE_POLICY' in os.environ"
            "}, sys.stdout); sys.stdout.write('\\n'); sys.stdout.flush()"
        )
        command = [sys.executable, "-c", script]

        def run_server():
            try:
                socket_bridge.serve_connection(server_socket, command)
            except BaseException as exc:
                errors.put(exc)
                server_socket.close()

        with mock.patch.dict(
            os.environ,
            {
                "OP_SERVICE_ACCOUNT_TOKEN": "synthetic-service-token",
                "CANVAS_WRITE_POLICY": "/private/tmp/synthetic-policy.json",
            },
        ):
            server_thread = threading.Thread(target=run_server)
            server_thread.start()
            with client_socket:
                response = client_socket.makefile("rb").readline()
            server_thread.join(timeout=2)

        self.assertFalse(server_thread.is_alive())
        if not errors.empty():
            raise errors.get()
        self.assertEqual(
            json.loads(response),
            {"service_token": False, "write_policy": False},
        )


if __name__ == "__main__":
    unittest.main()

import io
import json
import os
from pathlib import Path
import tempfile
import unittest
from unittest.mock import patch

from canvas_mcp.connection_config import ConnectionConfigError, configure_connection, load_connection_config
from canvas_mcp import server


class ConnectionConfigTests(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.addCleanup(self.temp.cleanup)
        self.path = Path(self.temp.name) / "connection.json"
        self.environment = patch.dict(os.environ, {}, clear=True)
        self.environment.start()
        self.addCleanup(self.environment.stop)

    def write_config(self, value):
        self.path.write_text(json.dumps(value))
        self.path.chmod(0o600)
        return str(self.path)

    def test_empty_host_environment_restores_reads_not_writes(self):
        config = {"CANVAS_BASE_URL": "https://school.example", "CANVAS_OP_ITEM": "test-item"}
        load_connection_config(self.write_config(config))
        with patch.object(server, "canvas_token", return_value="mock-token"), \
                patch.object(server.urllib.request, "urlopen") as urlopen:
            response = urlopen.return_value.__enter__.return_value
            response.read.return_value = b'{"id":123,"name":"Test course"}'
            result = server.api_get("/api/v1/courses/123")
            self.assertEqual(result["name"], "Test course")
            request = urlopen.call_args.args[0]
            self.assertEqual(request.full_url, "https://school.example/api/v1/courses/123")
            self.assertEqual(request.get_method(), "GET")
        self.assertNotIn("CANVAS_WRITE_POLICY", os.environ)
        self.assertFalse(server.write_policy()["enabled"])

    def test_forbidden_keys_never_change_environment(self):
        for key in ["CANVAS_TOKEN", "OP_SERVICE_ACCOUNT_TOKEN", "CANVAS_WRITE_POLICY",
                    "CANVAS_IMAGE_UPLOAD_ROOT", "CANVAS_POWERPOINT_UPLOAD_ROOT", "PATH", "other"]:
            with self.subTest(key=key):
                path = self.write_config({"CANVAS_BASE_URL": "https://school.example", key: "test-value"})
                with self.assertRaises(ConnectionConfigError):
                    load_connection_config(path)
                self.assertEqual(dict(os.environ), {})

    def test_conflicting_origins_and_lookup_references_are_atomic(self):
        for key, existing in [("CANVAS_BASE_URL", "https://other.example"), ("CANVAS_OP_ITEM", "other-item")]:
            with self.subTest(key=key), patch.dict(os.environ, {key: existing}, clear=True):
                path = self.write_config({"CANVAS_BASE_URL": "https://school.example", "CANVAS_OP_ITEM": "test-item"})
                with self.assertRaises(ConnectionConfigError):
                    load_connection_config(path)
                self.assertEqual(dict(os.environ), {key: existing})

    def test_matching_environment_preserves_existing_write_policy(self):
        original = {"CANVAS_BASE_URL": "https://school.example/", "CANVAS_WRITE_POLICY": "/test/policy.json"}
        with patch.dict(os.environ, original, clear=True):
            load_connection_config(self.write_config({"CANVAS_BASE_URL": "https://school.example"}))
            self.assertEqual(dict(os.environ), original)

    def test_rejects_invalid_origin_or_values(self):
        for value in ["http://school.example", "https://school.example/path", "https://u:p@school.example",
                      "https://school.example?q=x", "https://school.example#x", "https://[bad",
                      "https://:443", "https://school.example:bad", "https://school.example:99999",
                      "https://bad host", "", 1, None, "https://school.example\n"]:
            with self.subTest(value=value):
                with self.assertRaises(ConnectionConfigError):
                    load_connection_config(self.write_config({"CANVAS_BASE_URL": value}))
                self.assertEqual(dict(os.environ), {})

    def test_non_encodable_value_is_rejected_before_any_environment_mutation(self):
        path = self.write_config({"CANVAS_BASE_URL": "https://school.example", "CANVAS_OP_ITEM": "\ud800"})
        with self.assertRaises(ConnectionConfigError):
            load_connection_config(path)
        self.assertEqual(dict(os.environ), {})

    def test_requires_owned_private_regular_file(self):
        path = self.write_config({"CANVAS_BASE_URL": "https://school.example"})
        self.path.chmod(0o644)
        with self.assertRaises(ConnectionConfigError):
            load_connection_config(path)
        self.path.chmod(0o600)
        with patch("canvas_mcp.connection_config.os.getuid", return_value=os.getuid() + 1):
            with self.assertRaises(ConnectionConfigError):
                load_connection_config(path)
        link = self.path.with_name("link.json")
        link.symlink_to(self.path)
        with self.assertRaises(ConnectionConfigError):
            load_connection_config(str(link))
        fifo = self.path.with_name("fifo")
        os.mkfifo(fifo, 0o600)
        with self.assertRaises(ConnectionConfigError):
            load_connection_config(str(fifo))

    def test_rejects_relative_missing_malformed_duplicate_and_large_files(self):
        for path in ["relative.json", str(self.path)]:
            with self.assertRaises(ConnectionConfigError):
                load_connection_config(path)
        for raw in [b'not-json', b'\xff', b'[]', b'{}',
                    b'{"CANVAS_BASE_URL":"https://one.example","CANVAS_BASE_URL":"https://two.example"}', b' ' * 16385]:
            self.path.write_bytes(raw)
            self.path.chmod(0o600)
            with self.subTest(raw=raw[:20]), self.assertRaises(ConnectionConfigError):
                load_connection_config(str(self.path))

    def test_cli_loads_config_before_serving_and_keeps_protocol_stdout_clean(self):
        path = self.write_config({"CANVAS_BASE_URL": "https://school.example"})
        request = '{"jsonrpc":"2.0","id":1,"method":"initialize","params":{}}\n'
        with patch("sys.argv", ["canvas_mcp", "--connection-config", path]), \
                patch("sys.stdin", io.StringIO(request)), patch("sys.stdout", new_callable=io.StringIO) as output:
            server.main()
        result = json.loads(output.getvalue())
        self.assertEqual(result["id"], 1)
        self.assertEqual(server.canvas_base_url(), "https://school.example")

    def test_cli_rejects_configuration_without_echoing_values(self):
        path = self.write_config({"CANVAS_BASE_URL": "https://school.example", "CANVAS_TOKEN": "fake-private-value"})
        with patch("sys.argv", ["canvas_mcp", "--connection-config", path]), \
                patch("sys.stderr", new_callable=io.StringIO) as error, \
                patch("sys.stdout", new_callable=io.StringIO) as output, self.assertRaises(SystemExit) as raised:
            server.main()
        self.assertEqual(raised.exception.code, 2)
        self.assertNotIn("fake-private-value", error.getvalue())
        self.assertEqual(output.getvalue(), "")

    def test_cached_launch_without_arguments_uses_private_default_only_if_base_missing(self):
        default = Path(self.temp.name) / ".config" / "canvas-mcp" / "connection.json"
        default.parent.mkdir(parents=True)
        self.path = default
        self.write_config({"CANVAS_BASE_URL": "https://school.example"})
        with patch("canvas_mcp.connection_config.Path.home", return_value=Path(self.temp.name)):
            with patch("sys.argv", ["canvas_mcp"]), patch("sys.stdin", io.StringIO("")):
                server.main()
            self.assertEqual(server.canvas_base_url(), "https://school.example")
            os.environ["CANVAS_BASE_URL"] = "https://configured.example"
            configure_connection(None)
            self.assertEqual(server.canvas_base_url(), "https://configured.example")
        self.assertFalse(server.write_policy()["enabled"])

    def test_default_missing_is_unchanged_and_unsafe_default_fails_closed(self):
        with patch("canvas_mcp.connection_config.Path.home", return_value=Path(self.temp.name)):
            configure_connection(None)
            self.assertEqual(dict(os.environ), {})
            default = Path(self.temp.name) / ".config" / "canvas-mcp" / "connection.json"
            default.parent.mkdir(parents=True)
            default.symlink_to(default.with_name("missing.json"))
            with self.assertRaises(ConnectionConfigError):
                configure_connection(None)

    def test_explicit_missing_file_never_falls_back(self):
        with patch("canvas_mcp.connection_config.Path.home") as home:
            with self.assertRaises(ConnectionConfigError):
                configure_connection(str(self.path))
            home.assert_not_called()

    def test_inaccessible_default_fails_closed(self):
        with patch("canvas_mcp.connection_config.Path.home", return_value=Path(self.temp.name)), \
                patch("canvas_mcp.connection_config.Path.lstat", side_effect=PermissionError):
            with self.assertRaises(ConnectionConfigError):
                configure_connection(None)

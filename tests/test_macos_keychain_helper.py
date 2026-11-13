from pathlib import Path
import os
import shutil
import subprocess
import sys
import tempfile
import unittest


REPOSITORY_ROOT = Path(__file__).resolve().parents[1]
HELPER = REPOSITORY_ROOT / "scripts" / "store_service_token_macos.swift"
LAUNCH_AGENT_EXAMPLE = REPOSITORY_ROOT / "examples" / "com.example.codex-canvas-mcp.plist"


@unittest.skipUnless(sys.platform == "darwin", "macOS Keychain helper")
class MacOSKeychainHelperTests(unittest.TestCase):
    def test_helper_typechecks(self):
        swiftc = shutil.which("swiftc")
        if swiftc is None:
            self.skipTest("swiftc is not installed")
        with tempfile.TemporaryDirectory(dir="/private/tmp") as cache:
            environment = dict(os.environ)
            environment["CLANG_MODULE_CACHE_PATH"] = cache
            environment["SWIFT_MODULECACHE_PATH"] = cache
            completed = subprocess.run(
                [swiftc, "-typecheck", str(HELPER)],
                capture_output=True,
                text=True,
                check=False,
                env=environment,
            )
        self.assertEqual(completed.returncode, 0, completed.stderr)

    def test_helper_preserves_acl_and_never_passes_token_to_security_cli(self):
        source = HELPER.read_text(encoding="utf-8")
        self.assertIn("SecItemUpdate", source)
        self.assertIn("SecItemCopyMatching", source)
        self.assertIn("kSecMatchLimitAll", source)
        self.assertIn("kSecMatchItemList", source)
        self.assertIn("tcsetattr", source)
        self.assertNotIn("add-generic-password", source)
        self.assertNotIn('"/usr/bin/security"', source)
        self.assertNotIn("--token", source)

    def test_helper_refuses_noninteractive_token_input(self):
        xcrun = shutil.which("xcrun")
        if xcrun is None:
            self.skipTest("xcrun is not installed")
        token = "ops_" + ("synthetic" * 4)
        completed = subprocess.run(
            [
                xcrun,
                "swift",
                str(HELPER),
                "--service",
                "synthetic-service",
                "--account",
                "synthetic-account",
                "--vault",
                "synthetic-vault",
                "--item",
                "synthetic-item",
                "--op-path",
                "/usr/bin/false",
            ],
            input=token + "\n",
            capture_output=True,
            text=True,
            check=False,
        )
        self.assertNotEqual(completed.returncode, 0)
        self.assertIn("without an interactive TTY", completed.stderr)
        self.assertNotIn(token, completed.stdout + completed.stderr)

    def test_launch_agent_example_is_valid_and_contains_no_secret_or_write_policy(self):
        plutil = shutil.which("plutil")
        if plutil is None:
            self.skipTest("plutil is not installed")
        completed = subprocess.run(
            [plutil, "-lint", str(LAUNCH_AGENT_EXAMPLE)],
            capture_output=True,
            text=True,
            check=False,
        )
        self.assertEqual(completed.returncode, 0, completed.stdout + completed.stderr)
        source = LAUNCH_AGENT_EXAMPLE.read_text(encoding="utf-8")
        self.assertIn(
            "/Library/Application Support/codex-canvas-mcp/runtime/venv/bin/python",
            source,
        )
        self.assertNotIn(
            "/absolute/path/to/codex_canvas_mcp/.venv/bin/python",
            source,
        )
        self.assertNotIn("OP_SERVICE_ACCOUNT_TOKEN", source)
        self.assertNotIn("CANVAS_WRITE_POLICY", source)


if __name__ == "__main__":
    unittest.main()

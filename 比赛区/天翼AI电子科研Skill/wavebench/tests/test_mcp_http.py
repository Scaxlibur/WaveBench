import http.client
import json
import threading
import unittest
import urllib.error
import urllib.request
from pathlib import Path
from tempfile import TemporaryDirectory

from wavebench import __version__
from wavebench.cli import build_parser
from wavebench.errors import ConfigError
from wavebench.mcp_http import (
    DEFAULT_MCP_HOST,
    MAX_MCP_BODY_BYTES,
    make_mcp_http_server,
    resolve_mcp_token,
    validate_mcp_host,
)


class McpHttpTests(unittest.TestCase):
    def _write_config(self, root: Path) -> Path:
        path = root / "wavebench.toml"
        path.write_text(
            """
[connection]
resource = "TCPIP::scope::INSTR"

[scope]

[source]
resource = "TCPIP::source::INSTR"

[safety_limits]
max_source_vpp = 10.0
""",
            encoding="utf-8",
        )
        return path

    def _write_plan(self, root: Path) -> Path:
        plans = root / "plans"
        plans.mkdir(exist_ok=True)
        path = plans / "plan.toml"
        path.write_text(
            """
[[steps]]
kind = "source.set_vpp"
channel = 2
value_vpp = 1.0
""",
            encoding="utf-8",
        )
        return path

    def _write_capture(self, root: Path) -> Path:
        captures = root / "data" / "raw"
        captures.mkdir(parents=True, exist_ok=True)
        path = captures / "capture"
        path.mkdir()
        (path / "metadata.json").write_text(
            json.dumps(
                {
                    "operation": {"command": "scope capture", "channel": 1},
                    "waveform": {
                        "summary": {
                            "channel": 1,
                            "samples": 1000,
                            "voltage_vpp_v": 3.3,
                        }
                    },
                    "files": {"npy": str(path / "ch1.npy")},
                }
            ),
            encoding="utf-8",
        )
        return path

    def _start_server(self, config_path: Path, token: str = "test-token"):
        server = make_mcp_http_server(
            host="127.0.0.1",
            port=0,
            token=token,
            config_path=config_path,
        )
        thread = threading.Thread(target=server.serve_forever, daemon=True)
        thread.start()
        self.addCleanup(server.server_close)
        self.addCleanup(server.shutdown)
        return server

    def _request(self, server, method: str, path: str, token: str | None = None, body=None):
        url = f"http://127.0.0.1:{server.server_address[1]}{path}"
        data = None if body is None else json.dumps(body).encode("utf-8")
        request = urllib.request.Request(url, data=data, method=method)
        if token is not None:
            request.add_header("Authorization", f"Bearer {token}")
        if body is not None:
            request.add_header("Content-Type", "application/json")
        with urllib.request.urlopen(request, timeout=5) as response:
            return response.status, json.loads(response.read().decode("utf-8"))

    def _raw_request(
        self,
        server,
        method: str,
        path: str,
        *,
        token: str | None = None,
        data: bytes = b"",
        content_type: str | None = "application/json",
    ):
        url = f"http://127.0.0.1:{server.server_address[1]}{path}"
        request = urllib.request.Request(url, data=data, method=method)
        if token is not None:
            request.add_header("Authorization", f"Bearer {token}")
        if content_type is not None:
            request.add_header("Content-Type", content_type)
        with urllib.request.urlopen(request, timeout=5) as response:
            return response.status, response.read().decode("utf-8")

    def test_cli_default_host_is_localhost(self):
        args = build_parser().parse_args(["mcp", "serve", "--token", "abc"])
        self.assertEqual(args.domain, "mcp")
        self.assertEqual(args.command, "serve")
        self.assertEqual(args.host, DEFAULT_MCP_HOST)
        self.assertEqual(args.host, "127.0.0.1")

    def test_rejects_unspecified_all_interfaces_host(self):
        with self.assertRaisesRegex(ConfigError, "0.0.0.0"):
            validate_mcp_host("0.0.0.0")

    def test_missing_token_is_rejected(self):
        with self.assertRaisesRegex(ConfigError, "token"):
            resolve_mcp_token(None, None)

    def test_health_is_available_without_token(self):
        with TemporaryDirectory() as tmp:
            server = self._start_server(self._write_config(Path(tmp)))

            status, payload = self._request(server, "GET", "/health")

            self.assertEqual(status, 200)
            self.assertEqual(payload["status"], "ok")
            self.assertTrue(payload["read_only"])

    def test_tools_requires_token(self):
        with TemporaryDirectory() as tmp:
            server = self._start_server(self._write_config(Path(tmp)))

            with self.assertRaises(urllib.error.HTTPError) as caught:
                self._request(server, "GET", "/tools")

            self.assertEqual(caught.exception.code, 401)

    def test_tools_lists_only_read_only_mvp_tools(self):
        with TemporaryDirectory() as tmp:
            server = self._start_server(self._write_config(Path(tmp)))

            status, payload = self._request(server, "GET", "/tools", token="test-token")

            self.assertEqual(status, 200)
            names = {tool["name"] for tool in payload["tools"]}
            self.assertEqual(names, {"run.schema", "run.check", "capture.inspect"})
            self.assertFalse(any("raw" in name.lower() for name in names))
            self.assertFalse(any("output" in name.lower() for name in names))
            self.assertFalse(any(name.lower().endswith((".on", ".off")) for name in names))

    def test_call_run_schema_succeeds(self):
        with TemporaryDirectory() as tmp:
            server = self._start_server(self._write_config(Path(tmp)))

            status, payload = self._request(
                server,
                "POST",
                "/call",
                token="test-token",
                body={"tool": "run.schema", "arguments": {}},
            )

            self.assertEqual(status, 200)
            self.assertIn("scope.capture", payload["result"]["schema_text"])
            self.assertIn("schema", payload["result"])

    def test_call_run_check_succeeds_without_instrument_io(self):
        with TemporaryDirectory() as tmp:
            root = Path(tmp)
            config = self._write_config(root)
            plan = self._write_plan(root)
            server = self._start_server(config)

            status, payload = self._request(
                server,
                "POST",
                "/call",
                token="test-token",
                body={"tool": "run.check", "arguments": {"plan": str(plan)}},
            )

            self.assertEqual(status, 200)
            self.assertEqual(payload["result"]["status"], "ok")
            self.assertEqual(payload["result"]["plan"]["steps"][0]["kind"], "source.set_vpp")

    def test_call_run_check_rejects_plan_outside_plans_dir(self):
        with TemporaryDirectory() as tmp:
            root = Path(tmp)
            config = self._write_config(root)
            outside = root / "outside.toml"
            outside.write_text("[[steps]]\nkind = 'source.status'\n", encoding="utf-8")
            server = self._start_server(config)

            with self.assertRaises(urllib.error.HTTPError) as caught:
                self._request(
                    server,
                    "POST",
                    "/call",
                    token="test-token",
                    body={"tool": "run.check", "arguments": {"plan": str(outside)}},
                )

            self.assertEqual(caught.exception.code, 400)

    def test_call_run_check_rejects_non_toml_plan(self):
        with TemporaryDirectory() as tmp:
            root = Path(tmp)
            config = self._write_config(root)
            plans = root / "plans"
            plans.mkdir()
            bad_plan = plans / "plan.txt"
            bad_plan.write_text("[[steps]]\nkind = 'source.status'\n", encoding="utf-8")
            server = self._start_server(config)

            with self.assertRaises(urllib.error.HTTPError) as caught:
                self._request(
                    server,
                    "POST",
                    "/call",
                    token="test-token",
                    body={"tool": "run.check", "arguments": {"plan": str(bad_plan)}},
                )

            self.assertEqual(caught.exception.code, 400)

    def test_call_capture_inspect_succeeds_offline(self):
        with TemporaryDirectory() as tmp:
            root = Path(tmp)
            server = self._start_server(self._write_config(root))
            capture = self._write_capture(root)

            status, payload = self._request(
                server,
                "POST",
                "/call",
                token="test-token",
                body={"tool": "capture.inspect", "arguments": {"path": str(capture)}},
            )

            self.assertEqual(status, 200)
            self.assertEqual(payload["result"]["channels"][0]["channel"], 1)
            self.assertEqual(payload["result"]["channels"][0]["summary"]["samples"], 1000)


    def test_call_capture_inspect_rejects_path_outside_data_raw(self):
        with TemporaryDirectory() as tmp:
            root = Path(tmp)
            config = self._write_config(root)
            outside = root / "capture"
            outside.mkdir()
            (outside / "metadata.json").write_text(
                json.dumps({"operation": {}, "waveform": {"summary": {}}}),
                encoding="utf-8",
            )
            server = self._start_server(config)

            with self.assertRaises(urllib.error.HTTPError) as caught:
                self._request(
                    server,
                    "POST",
                    "/call",
                    token="test-token",
                    body={"tool": "capture.inspect", "arguments": {"path": str(outside)}},
                )

            self.assertEqual(caught.exception.code, 400)

    def test_call_capture_inspect_rejects_traversal_out_of_data_raw(self):
        with TemporaryDirectory() as tmp:
            root = Path(tmp)
            config = self._write_config(root)
            server = self._start_server(config)

            with self.assertRaises(urllib.error.HTTPError) as caught:
                self._request(
                    server,
                    "POST",
                    "/call",
                    token="test-token",
                    body={
                        "tool": "capture.inspect",
                        "arguments": {"path": "data/raw/../../capture"},
                    },
                )

            self.assertEqual(caught.exception.code, 400)

    def test_mcp_jsonrpc_initialize_reports_package_version(self):
        with TemporaryDirectory() as tmp:
            server = self._start_server(self._write_config(Path(tmp)))

            status, payload = self._request(
                server,
                "POST",
                "/mcp",
                token="test-token",
                body={"jsonrpc": "2.0", "id": 1, "method": "initialize", "params": {}},
            )

            self.assertEqual(status, 200)
            self.assertEqual(payload["result"]["serverInfo"]["name"], "wavebench")
            self.assertEqual(payload["result"]["serverInfo"]["version"], __version__)

    def test_mcp_jsonrpc_tools_list_and_call(self):
        with TemporaryDirectory() as tmp:
            root = Path(tmp)
            server = self._start_server(self._write_config(root))
            plan = self._write_plan(root)

            status, listed = self._request(
                server,
                "POST",
                "/mcp",
                token="test-token",
                body={"jsonrpc": "2.0", "id": 1, "method": "tools/list", "params": {}},
            )
            self.assertEqual(status, 200)
            names = {tool["name"] for tool in listed["result"]["tools"]}
            self.assertEqual(names, {"run.schema", "run.check", "capture.inspect"})

            status, called = self._request(
                server,
                "POST",
                "/mcp",
                token="test-token",
                body={
                    "jsonrpc": "2.0",
                    "id": 2,
                    "method": "tools/call",
                    "params": {"name": "run.check", "arguments": {"plan": str(plan)}},
                },
            )
            self.assertEqual(status, 200)
            self.assertEqual(called["result"]["structuredContent"]["status"], "ok")
            self.assertEqual(called["result"]["content"][0]["type"], "text")

    def test_mcp_jsonrpc_requires_token(self):
        with TemporaryDirectory() as tmp:
            server = self._start_server(self._write_config(Path(tmp)))

            with self.assertRaises(urllib.error.HTTPError) as caught:
                self._request(
                    server,
                    "POST",
                    "/mcp",
                    body={"jsonrpc": "2.0", "id": 1, "method": "tools/list", "params": {}},
                )
            self.assertEqual(caught.exception.code, 401)

    def _raw_request_with_content_length(
        self, server, path: str, *, token: str, content_length: int
    ):
        connection = http.client.HTTPConnection(
            "127.0.0.1", server.server_address[1], timeout=5
        )
        try:
            connection.putrequest("POST", path)
            connection.putheader("Authorization", f"Bearer {token}")
            connection.putheader("Content-Type", "application/json")
            connection.putheader("Content-Length", str(content_length))
            connection.endheaders()
            response = connection.getresponse()
            return response.status, response.read().decode("utf-8")
        finally:
            connection.close()

    def test_call_rejects_body_larger_than_limit(self):
        with TemporaryDirectory() as tmp:
            server = self._start_server(self._write_config(Path(tmp)))

            status, raw = self._raw_request_with_content_length(
                server, "/call", token="test-token", content_length=MAX_MCP_BODY_BYTES + 1
            )
            payload = json.loads(raw)

            self.assertEqual(status, 400)
            self.assertEqual(payload["error"]["type"], "ConfigError")

    def test_mcp_jsonrpc_reports_large_body_as_error(self):
        with TemporaryDirectory() as tmp:
            server = self._start_server(self._write_config(Path(tmp)))

            status, raw = self._raw_request_with_content_length(
                server, "/mcp", token="test-token", content_length=MAX_MCP_BODY_BYTES + 1
            )
            payload = json.loads(raw)

            self.assertEqual(status, 200)
            self.assertEqual(payload["error"]["code"], -32602)
            self.assertEqual(payload["error"]["data"]["type"], "ConfigError")

    def test_mcp_jsonrpc_rejects_plan_outside_plans_dir(self):
        with TemporaryDirectory() as tmp:
            root = Path(tmp)
            server = self._start_server(self._write_config(root))
            outside = root / "outside.toml"
            outside.write_text("[[steps]]\nkind = 'source.status'\n", encoding="utf-8")

            status, payload = self._request(
                server,
                "POST",
                "/mcp",
                token="test-token",
                body={
                    "jsonrpc": "2.0",
                    "id": 9,
                    "method": "tools/call",
                    "params": {"name": "run.check", "arguments": {"plan": str(outside)}},
                },
            )

            self.assertEqual(status, 200)
            self.assertEqual(payload["error"]["code"], -32602)
            self.assertEqual(payload["error"]["data"]["type"], "ConfigError")

    def test_unknown_and_write_like_tools_are_rejected(self):
        with TemporaryDirectory() as tmp:
            server = self._start_server(self._write_config(Path(tmp)))

            for tool in ("raw.scpi", "source.output", "power.output"):
                with self.subTest(tool=tool):
                    with self.assertRaises(urllib.error.HTTPError) as caught:
                        self._request(
                            server,
                            "POST",
                            "/call",
                            token="test-token",
                            body={"tool": tool, "arguments": {}},
                        )
                    self.assertEqual(caught.exception.code, 404)


if __name__ == "__main__":
    unittest.main()

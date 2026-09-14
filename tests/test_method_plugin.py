"""Unit tests for ``type: plugin:*`` method plugins (serve-plugin-methods todo 4).

Covers the ``execute_plugin_method`` kernel (``builder.py``) through both
entry paths — the Click callback (``_make_plugin_callback``) and the
``execute_pipeline`` branch — plus the server file jail and spec-local
plugin discovery.

Isolation (pinned): every test registers its methods inline via
``register_method``; an autouse fixture clears ``PluginRegistry`` and
restores the ``_scanned_dirs`` discovery cache around each test, so this
file is order-independent within the full suite.  No ``tests/fixtures``
Python imports (side-effect free).
"""

from __future__ import annotations

import json
import uuid
from typing import Any

import pytest
from click.testing import CliRunner

from cliyard.engine.builder import (
    ServiceContext,
    build_operation_command,
    execute_pipeline,
)
from cliyard.engine.errors import CliyError, PluginNotFoundError, ValidationError
from cliyard.plugin import PluginRegistry, register_method
from cliyard.plugin.discovery import _scanned_dirs


@pytest.fixture(autouse=True)
def _isolate_method_plugins():
    """Snapshot discovery cache, clear registry; restore afterwards."""
    scanned_snapshot = set(_scanned_dirs)
    PluginRegistry.clear()
    yield
    PluginRegistry.clear()
    _scanned_dirs.clear()
    _scanned_dirs.update(scanned_snapshot)


def _ctx() -> ServiceContext:
    return ServiceContext(base_url="http://127.0.0.1:9", auth_spec=None)


def _resource() -> dict[str, Any]:
    return {"name": "t", "path": "t"}


def _echo_spec(name: str, **params: Any) -> dict[str, Any]:
    return {
        "type": f"plugin:{name}",
        "config": {"k": "v"},
        "params": params,
    }


# ---------------------------------------------------------------------------
# CLI callback path
# ---------------------------------------------------------------------------


class TestPluginCliCallback:
    """register → CLI 回调输出断言。"""

    def test_cli_echo_params(self) -> None:
        @register_method("ut_echo")
        def _echo(params, http_client, config):
            return {"echo": params, "cfg": config}

        spec = _echo_spec(
            "ut_echo",
            body=[{"name": "name", "type": "string"}],
            query=[{"name": "q", "type": "string"}],
        )
        cmd = build_operation_command("do", spec, _resource(), _ctx())
        result = CliRunner().invoke(cmd, ["--name", "alice", "--q", "hi"], obj={})
        assert result.exit_code == 0, result.output
        data = json.loads(result.output)
        assert data["echo"]["name"] == "alice"  # body 上浮顶层
        assert data["echo"]["query"] == {"q": "hi"}  # query 留嵌套
        assert data["cfg"] == {"k": "v"}

    def test_cli_formatted_silent(self) -> None:
        @register_method("ut_fmt")
        def _fmt(params, http_client, config):
            return {"_formatted": True, "anything": 1}

        cmd = build_operation_command("do", _echo_spec("ut_fmt"), _resource(), _ctx())
        result = CliRunner().invoke(cmd, [], obj={})
        assert result.exit_code == 0, result.output
        assert result.output == ""

    def test_cli_unknown_plugin(self) -> None:
        """反例：未知插件名打印原文，exit 0。"""
        cmd = build_operation_command(
            "do", _echo_spec("ut_no_such"), _resource(), _ctx()
        )
        result = CliRunner().invoke(cmd, [], obj={})
        assert result.exit_code == 0
        assert "Plugin method 'ut_no_such' not found" in result.output

    def test_cli_cliy_error(self) -> None:
        @register_method("ut_cliy")
        def _boom(params, http_client, config):
            raise CliyError("bad thing [x]")

        cmd = build_operation_command("do", _echo_spec("ut_cliy"), _resource(), _ctx())
        result = CliRunner().invoke(cmd, [], obj={})
        assert result.exit_code == 0
        assert "bad thing" in result.output

    def test_cli_raw_exception(self) -> None:
        """反例：非 CliyError 异常走 `错误:` 前缀。"""
        @register_method("ut_raw")
        def _boom(params, http_client, config):
            raise ValueError("raw fail")

        cmd = build_operation_command("do", _echo_spec("ut_raw"), _resource(), _ctx())
        result = CliRunner().invoke(cmd, [], obj={})
        assert result.exit_code == 0
        assert "错误:" in result.output
        assert "raw fail" in result.output

    def test_cli_missing_required(self) -> None:
        """反例：缺必填参数（click 层直接拒绝）。"""
        @register_method("ut_req")
        def _echo(params, http_client, config):
            return {"echo": params}

        spec = _echo_spec(
            "ut_req", body=[{"name": "name", "type": "string", "required": True}]
        )
        cmd = build_operation_command("do", spec, _resource(), _ctx())
        result = CliRunner().invoke(cmd, [], obj={})
        assert result.exit_code != 0
        assert "Missing option" in result.output


# ---------------------------------------------------------------------------
# execute_pipeline branch
# ---------------------------------------------------------------------------


class TestPluginPipelineBranch:
    """pipeline 分支 happy / unknown / merge parity。"""

    def test_pipeline_happy_equals_cli(self) -> None:
        @register_method("ut_pecho")
        def _echo(params, http_client, config):
            return {"echo": params, "cfg": config}

        spec = _echo_spec(
            "ut_pecho", body=[{"name": "name", "type": "string"}]
        )
        via_pipeline = execute_pipeline(
            {"name": "alice"}, dict(spec), _resource(), _ctx()
        )
        cmd = build_operation_command("do", dict(spec), _resource(), _ctx())
        cli_result = CliRunner().invoke(cmd, ["--name", "alice"], obj={})
        assert cli_result.exit_code == 0, cli_result.output
        assert json.loads(cli_result.output) == via_pipeline

    def test_pipeline_no_http_block(self) -> None:
        """无 http 块的方法不再报 `http.method is required`。"""
        @register_method("ut_nohttp")
        def _echo(params, http_client, config):
            return {"ok": True}

        spec = {"type": "plugin:ut_nohttp", "params": {}}
        assert execute_pipeline({}, spec, _resource(), _ctx()) == {"ok": True}

    def test_pipeline_unknown_raises(self) -> None:
        """反例：未知插件名抛 PluginNotFoundError（含名，CliyError 子类）。"""
        assert issubclass(PluginNotFoundError, CliyError)
        with pytest.raises(PluginNotFoundError) as exc_info:
            execute_pipeline(
                {}, {"type": "plugin:ut_ghost"}, _resource(), _ctx()
            )
        assert "ut_ghost" in str(exc_info.value)

    def test_pipeline_query_merge_parity(self) -> None:
        """query 值只进嵌套，不进顶层（仅 path/body/argument 上浮）。"""
        @register_method("ut_qmerge")
        def _echo(params, http_client, config):
            return {"echo": params}

        spec = _echo_spec(
            "ut_qmerge", query=[{"name": "qq", "type": "string", "required": True}]
        )
        result = execute_pipeline({"qq": "topcheck"}, spec, _resource(), _ctx())
        assert result["echo"]["query"] == {"qq": "topcheck"}
        assert "qq" not in result["echo"]

    def test_pipeline_missing_required(self) -> None:
        """反例：缺必填参数 → ValidationError（CliyError）。"""
        @register_method("ut_preq")
        def _echo(params, http_client, config):
            return {"echo": params}  # pragma: no cover

        spec = _echo_spec(
            "ut_preq", body=[{"name": "name", "type": "string", "required": True}]
        )
        with pytest.raises(ValidationError):
            execute_pipeline({}, spec, _resource(), _ctx())

    def test_pipeline_formatted_stripped(self) -> None:
        """`_formatted` 去 marker 返内容；raw_response 同样原样（去 marker 后）。"""
        @register_method("ut_pfmt")
        def _fmt(params, http_client, config):
            return {"_formatted": True, "v": 1}

        spec = {"type": "plugin:ut_pfmt", "params": {}}
        assert execute_pipeline({}, dict(spec), _resource(), _ctx()) == {"v": 1}
        assert execute_pipeline(
            {}, dict(spec), _resource(), _ctx(), raw_response=True
        ) == {"v": 1}


# ---------------------------------------------------------------------------
# File jail
# ---------------------------------------------------------------------------


class TestPluginFileJail:
    """server_mode 双向：True 拦（零调用）/ False 放行。"""

    def _file_spec(self, name: str) -> dict[str, Any]:
        return _echo_spec(name, body=[{"name": "f", "type": "file"}])

    def test_jail_blocks_with_zero_invocation(self, tmp_path) -> None:
        """反例：越狱路径 → CliyError，且插件零调用。"""
        calls: list[int] = []

        @register_method("ut_jail")
        def _count(params, http_client, config):
            calls.append(1)
            return {"ok": True}  # pragma: no cover

        upload_dir = tmp_path / "uploads"
        upload_dir.mkdir()
        with pytest.raises(CliyError):
            execute_pipeline(
                {"f": "/etc/hosts"},
                self._file_spec("ut_jail"),
                _resource(),
                _ctx(),
                server_mode=True,
                upload_dir=str(upload_dir),
            )
        assert calls == []

    def test_jail_tmp_bypass(self, tmp_path) -> None:
        """桥接产物（server_tmp_files）逐元素 bypass。"""
        @register_method("ut_bypass")
        def _ok(params, http_client, config):
            return {"ok": True}

        upload_dir = tmp_path / "uploads"
        upload_dir.mkdir()
        result = execute_pipeline(
            {"f": "/etc/hosts"},
            self._file_spec("ut_bypass"),
            _resource(),
            _ctx(),
            server_mode=True,
            upload_dir=str(upload_dir),
            server_tmp_files=["/etc/hosts"],
        )
        assert result == {"ok": True}

    def test_jail_cli_mode_passthrough(self) -> None:
        """CLI（server_mode=False）同参成功，零变化。"""
        @register_method("ut_cli_pass")
        def _ok(params, http_client, config):
            return {"ok": True}

        result = execute_pipeline(
            {"f": "/etc/hosts"},
            self._file_spec("ut_cli_pass"),
            _resource(),
            _ctx(),
        )
        assert result == {"ok": True}


# ---------------------------------------------------------------------------
# Spec-local discovery
# ---------------------------------------------------------------------------


class TestPluginSpecLocalDiscovery:
    """仅 fixture 插件目录可见时直调成功（显式 spec_dir 生效）。"""

    def _write_spec_local_plugin(self, tmp_path, plugin_name: str) -> str:
        tag = uuid.uuid4().hex[:8]
        module_name = f"ut_speclocal_{tag}"
        plug_dir = tmp_path / "spec" / "plugins"
        plug_dir.mkdir(parents=True)
        (plug_dir / f"{module_name}.py").write_text(
            "from cliyard.plugin import register_method\n"
            f"@register_method({plugin_name!r})\n"
            "def _fn(params, http_client, config):\n"
            "    return {'echo': params}\n",
            encoding="utf-8",
        )
        return str(tmp_path / "spec")

    def test_spec_local_discovery(self, tmp_path) -> None:
        plugin_name = f"ut_speclocal_{uuid.uuid4().hex[:8]}"
        spec_dir = self._write_spec_local_plugin(tmp_path, plugin_name)
        upload_dir = tmp_path / "uploads"
        upload_dir.mkdir()
        spec = _echo_spec(plugin_name, body=[{"name": "name", "type": "string"}])
        result = execute_pipeline(
            {"name": "zed"},
            spec,
            _resource(),
            _ctx(),
            server_mode=True,
            upload_dir=str(upload_dir),
            spec_dir=spec_dir,
        )
        assert result["echo"]["name"] == "zed"

    def test_spec_local_missing_without_spec_dir(self, tmp_path) -> None:
        """反例：不传 spec_dir 时 spec-local 插件不可见。"""
        plugin_name = f"ut_nospec_{uuid.uuid4().hex[:8]}"
        self._write_spec_local_plugin(tmp_path, plugin_name)
        with pytest.raises(PluginNotFoundError):
            execute_pipeline(
                {"name": "zed"},
                _echo_spec(plugin_name),
                _resource(),
                _ctx(),
            )

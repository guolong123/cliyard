"""Grouped-mode MCP tool table + dispatch unit tests (plan todo 7).

Synthetic spec fixture (inline builders, tmp_path-only — no shared fixture
files, no servers, no fixed ports). Coverage:

* multi-method resource (``alpha`` × CRUD)；
  cross-group duplicate-name resource (``g1.token`` / ``g2.token``)；
  same-name-different-meaning field collision resource (``item``:
  ``filter`` int/query vs string/body + identical ``sort`` control)；
  file-param resource (``docs``: multipart ``upload``)；
  multi-namespace ``cmd.*`` plugins (``hello`` single + ``pkg`` group)；
  2 flows (``alpha_flow`` / ``docs_flow``).

Failure-first ordering: unknown-operation / missing-operation / missing-required
cases run FIRST (tests 01–03 assert the red isError shape), happy paths after.
Dispatch is never mocked — real ``execute_spec`` / ``build_tool_specs``; only
the HTTP boundary (``execute_pipeline``) is echoed back so no server is needed.
"""

from __future__ import annotations

import json
import re
from pathlib import Path

import anyio
import pytest

from cliyard.engine.errors import ValidationError
from cliyard.server.mcp.executor import MCPExecutor
from cliyard.server.mcp.tools import build_tool_specs

_AUTH_YAML = """\
name: grouped-probe
version: "1.0"
description: Synthetic spec for grouped MCP tool unit tests
server:
  base_url: http://127.0.0.1:1
"""

_ALPHA_YAML = """\
description: Alpha resources
path: alphas
methods:
  list:
    description: List alphas
    http: {method: GET}
    params:
      query:
        - name: page
          type: int
          default: 1
  get:
    description: Get one alpha
    http:
      method: GET
      path: alphas/{{ alpha_id }}
    params:
      path:
        - name: alpha_id
          type: string
          required: true
  create:
    description: Create an alpha
    http: {method: POST}
    params:
      body:
        - name: name
          type: string
          required: true
  delete:
    description: Delete an alpha
    http:
      method: DELETE
      path: alphas/{{ alpha_id }}
    params:
      path:
        - name: alpha_id
          type: string
          required: true
"""

_ITEM_YAML = """\
description: Item search index
path: items
methods:
  search:
    description: Search items by numeric filter
    http: {method: GET}
    params:
      query:
        - name: filter
          type: int
          required: true
          description: numeric filter code
        - name: sort
          type: string
          description: sort order
  create:
    description: Create an item with a label filter
    http: {method: POST}
    params:
      query:
        - name: sort
          type: string
          description: sort order
      body:
        - name: filter
          type: string
          required: true
          description: label filter text
"""

_DOCS_YAML = """\
description: Document store
path: docs
methods:
  list:
    description: List documents
    http: {method: GET}
  upload:
    description: Upload a document
    http: {method: POST}
    body_type: multipart
    params:
      body:
        - name: file
          type: file
          required: true
          description: document file
"""

_G1_TOKEN_YAML = """\
group: g1
name: token
description: G1 tokens
path: g1/tokens
methods:
  list:
    description: List g1 tokens
    http: {method: GET}
  create:
    description: Create g1 token
    http: {method: POST}
    params:
      body:
        - name: label
          type: string
          required: true
"""

_G2_TOKEN_YAML = """\
group: g2
name: token
description: G2 tokens
path: g2/tokens
methods:
  list:
    description: List g2 tokens
    http: {method: GET}
  delete:
    description: Delete g2 token
    http:
      method: DELETE
      path: g2/tokens/{{ token_id }}
    params:
      path:
        - name: token_id
          type: string
          required: true
"""

# Command-level plugins: same leaf shape as tests/fixtures/spec-plugins
# (``hello`` + ``pkg.{info,search}``) so the process-global PluginRegistry
# keeps identical keys for other test files; module/file names are unique.
_PLUGINS_PY = '''\
"""Inline cmd.* plugins for the grouped-tools synthetic spec."""

import click
from rich.console import Console

from cliyard.plugin import register_command

console = Console()


@register_command("hello")
def register_grouped_probe_hello(cli, ctx):
    @click.command("hello")
    @click.argument("name", required=True)
    @click.option("-g", "--greeting", default="Hello", help="Greeting word")
    def hello(name, greeting):
        """Greet someone."""
        console.print(f"{greeting}, {name}!")

    cli.add_command(hello)


@register_command("pkg")
def register_grouped_probe_pkg(cli, ctx):
    @click.group("pkg")
    def pkg_group():
        """Package demo group."""

    @pkg_group.command("info")
    @click.argument("package_name")
    @click.option("-v", "--verbose", is_flag=True, help="Show verbose info")
    def pkg_info(package_name, verbose):
        """Show package info."""
        console.print(f"pkg {package_name} verbose={verbose}")

    @pkg_group.command("search")
    @click.argument("keywords", nargs=-1, required=False)
    @click.option("-n", "--limit", type=int, default=10, help="Max results")
    def pkg_search(keywords, limit):
        """Search packages by keywords."""
        keys = list(keywords) or ["*"]
        console.print(f"search {keys} limit={limit}")

    cli.add_command(pkg_group)
'''

_FLOWS_YAML = """\
flows:
  alpha_flow:
    description: Alpha lifecycle demo
    command: alpha-flow
    steps: _flow_alpha.yaml
    params:
      query:
        - name: name
          type: string
          required: true
  docs_flow:
    description: Docs pipeline demo
    command: docs-flow
    steps: _flow_docs.yaml
"""

_FLOW_ALPHA_YAML = """\
steps:
  - id: fetch_alphas
    description: fetch alphas
    use: alpha.list
    params:
      page: 1
"""

_FLOW_DOCS_YAML = """\
steps:
  - id: fetch_docs
    description: fetch docs
    use: docs.list
"""


def _write_grouped_spec(root: Path) -> Path:
    """Build the synthetic spec dir under a unique tmp root; return its path."""
    spec = root / "gspec"
    (spec / "plugins").mkdir(parents=True)
    (spec / "flows").mkdir(parents=True)
    (spec / "_auth.yaml").write_text(_AUTH_YAML, encoding="utf-8")
    (spec / "alpha.yaml").write_text(_ALPHA_YAML, encoding="utf-8")
    (spec / "item.yaml").write_text(_ITEM_YAML, encoding="utf-8")
    (spec / "docs.yaml").write_text(_DOCS_YAML, encoding="utf-8")
    (spec / "g1_token.yaml").write_text(_G1_TOKEN_YAML, encoding="utf-8")
    (spec / "g2_token.yaml").write_text(_G2_TOKEN_YAML, encoding="utf-8")
    (spec / "plugins" / "grouped_probe_cmds.py").write_text(
        _PLUGINS_PY, encoding="utf-8"
    )
    (spec / "flows" / "_flows.yaml").write_text(_FLOWS_YAML, encoding="utf-8")
    (spec / "flows" / "_flow_alpha.yaml").write_text(
        _FLOW_ALPHA_YAML, encoding="utf-8"
    )
    (spec / "flows" / "_flow_docs.yaml").write_text(
        _FLOW_DOCS_YAML, encoding="utf-8"
    )
    return spec


@pytest.fixture()
def spec_dir(tmp_path: Path) -> Path:
    return _write_grouped_spec(tmp_path)


def _echo_pipeline(monkeypatch: pytest.MonkeyPatch) -> None:
    """Echo the HTTP boundary back (no servers); dispatch stays real."""

    def _fake(**kwargs):
        return {
            "ok": True,
            "resource": kwargs.get("resource_name"),
            "bridged": kwargs.get("kwargs"),
        }

    monkeypatch.setattr(
        "cliyard.server.mcp.executor.execute_pipeline", _fake
    )


def _call(ex: MCPExecutor, name: str, arguments: dict):
    params = type("P", (), {"name": name, "arguments": arguments})()

    async def _go():
        return await ex.call_tool(None, params)

    return anyio.run(_go)


# ---------------------------------------------------------------------------
# Failure-first: red isError shapes before any happy path
# ---------------------------------------------------------------------------


def test_01_unknown_operation_is_error_with_legal_list(spec_dir: Path):
    """Grouped call with a bogus operation → isError listing legal ops."""
    ex = MCPExecutor(spec_dir, tool_mode="grouped")
    result = _call(ex, "alpha", {"operation": "nope", "name": "x"})
    assert result.is_error is True
    text = result.content[0].text
    assert "Unknown operation 'nope'" in text
    for op in ("list", "get", "create", "delete"):
        assert op in text


def test_02_missing_operation_is_error_with_usage(spec_dir: Path):
    """Grouped call without operation → isError with usage + legal ops."""
    ex = MCPExecutor(spec_dir, tool_mode="grouped")
    result = _call(ex, "alpha", {"name": "x"})
    assert result.is_error is True
    text = result.content[0].text
    assert "requires 'operation'" in text
    assert "operation=<" in text
    for op in ("list", "get", "create", "delete"):
        assert op in text


def test_03_missing_required_same_wording_as_flat(spec_dir: Path):
    """Runtime required validation: grouped error text identical to flat."""
    ex_flat = MCPExecutor(spec_dir)  # flat
    ex_grouped = MCPExecutor(spec_dir, tool_mode="grouped")
    with pytest.raises(ValidationError) as flat_err:
        ex_flat.execute_spec(ex_flat.tool_specs["alpha.create"], {})
    with pytest.raises(ValidationError) as grouped_err:
        ex_grouped.execute_spec(
            ex_grouped.tool_specs["alpha"], {"operation": "create"}
        )
    assert str(grouped_err.value) == str(flat_err.value)
    assert str(flat_err.value) == "name: required"


# ---------------------------------------------------------------------------
# Happy paths: structure, parity, dispatch
# ---------------------------------------------------------------------------


def test_04_grouped_count_and_structure(spec_dir: Path):
    """grouped == resources + flows + plugin-namespaces; grouped << flat."""
    grouped = build_tool_specs(spec_dir, mode="grouped")
    flat = build_tool_specs(spec_dir, mode="flat")

    res_tools = {
        n for n, s in grouped.items()
        if s.kind == "grouped" and not n.startswith("cmd.")
    }
    assert res_tools == {"alpha", "item", "docs", "g1.token", "g2.token"}
    flow_tools = {n for n, s in grouped.items() if s.kind == "flow"}
    assert len(flow_tools) == 2
    plug_tools = {
        n for n, s in grouped.items()
        if s.kind == "grouped" and n.startswith("cmd.")
    }
    assert {"cmd.hello", "cmd.pkg"} <= plug_tools
    # namespaces 1:1 with flat cmd.* leaves
    flat_leaves = {n for n, s in flat.items() if s.kind == "plugin"}
    assert flat_leaves, "expected cmd.* plugin leaves in flat table"
    namespaces = {
        "cmd." + n.removeprefix("cmd.").split(".")[0] for n in flat_leaves
    }
    assert plug_tools == namespaces

    assert len(grouped) == len(res_tools) + len(flow_tools) + len(plug_tools)
    assert len(grouped) * 2 <= len(flat), (len(grouped), len(flat))

    # zero cross-mode leakage
    assert "alpha.create" not in grouped
    assert "alpha" not in flat
    for spec in grouped.values():
        if spec.kind == "grouped":
            assert spec.input_schema["required"] == ["operation"]


def test_05_operation_enum_matches_flat_method_set(spec_dir: Path):
    """Each grouped tool's operation enum is 1:1 with its flat method set."""
    grouped = build_tool_specs(spec_dir, mode="grouped")
    flat = build_tool_specs(spec_dir, mode="flat")
    res_tools = [
        (n, s) for n, s in grouped.items()
        if s.kind == "grouped" and not n.startswith("cmd.")
    ]
    assert len(res_tools) == 5
    for tool_name, spec in res_tools:
        enum = spec.input_schema["properties"]["operation"]["enum"]
        flat_ops = sorted(
            k[len(tool_name) + 1:]
            for k in flat
            if k.startswith(tool_name + ".") and flat[k].kind == "command"
        )
        assert sorted(enum) == flat_ops, tool_name
        assert set(spec.operations or {}) == set(enum)
        # carrier identity: tool.operation spells the flat name
        for op in enum:
            assert f"{tool_name}.{op}" in flat


def test_06_same_result_crud_resource(
    spec_dir: Path, monkeypatch: pytest.MonkeyPatch
):
    """CRUD resource: grouped vs flat same-param same-result via execute_spec."""
    _echo_pipeline(monkeypatch)
    ex_flat = MCPExecutor(spec_dir)
    ex_grouped = MCPExecutor(spec_dir, tool_mode="grouped")
    flat_result = ex_flat.execute_spec(
        ex_flat.tool_specs["alpha.create"], {"name": "n1"}
    )
    grouped_result = ex_grouped.execute_spec(
        ex_grouped.tool_specs["alpha"], {"operation": "create", "name": "n1"}
    )
    assert json.dumps(grouped_result, sort_keys=True, default=str) == json.dumps(
        flat_result, sort_keys=True, default=str
    )
    assert grouped_result["resource"] == "alpha"
    assert grouped_result["bridged"] == {"name": "n1"}


def test_07_same_result_file_resource(
    spec_dir: Path, tmp_path: Path, monkeypatch: pytest.MonkeyPatch
):
    """File resource: grouped vs flat same-param same-result via execute_spec."""
    _echo_pipeline(monkeypatch)
    payload = tmp_path / "report.txt"
    payload.write_text("grouped probe\n", encoding="utf-8")
    ex_flat = MCPExecutor(spec_dir)
    ex_grouped = MCPExecutor(spec_dir, tool_mode="grouped")
    flat_result = ex_flat.execute_spec(
        ex_flat.tool_specs["docs.upload"], {"file": str(payload)}
    )
    grouped_result = ex_grouped.execute_spec(
        ex_grouped.tool_specs["docs"],
        {"operation": "upload", "file": str(payload)},
    )
    assert json.dumps(grouped_result, sort_keys=True, default=str) == json.dumps(
        flat_result, sort_keys=True, default=str
    )
    assert grouped_result["bridged"] == {"file": str(payload)}


def test_08_same_result_plugin_command(spec_dir: Path):
    """Plugin command: grouped vs flat same-param same-result via execute_spec."""
    ex_flat = MCPExecutor(spec_dir)
    ex_grouped = MCPExecutor(spec_dir, tool_mode="grouped")
    args = {"keywords": ["a", "b"], "limit": 5}
    flat_result = ex_flat.execute_spec(
        ex_flat.tool_specs["cmd.pkg.search"], dict(args)
    )
    grouped_result = ex_grouped.execute_spec(
        ex_grouped.tool_specs["cmd.pkg"], {"operation": "search", **args}
    )
    assert grouped_result == flat_result
    assert "search ['a', 'b'] limit=5" in grouped_result


def test_09_collision_annotation_and_identical_field_quiet(spec_dir: Path):
    """keep-first + 遮蔽 annotation on collision; identical field unmarked."""
    grouped = build_tool_specs(spec_dir, mode="grouped")
    props = grouped["item"].input_schema["properties"]
    filt_desc = props["filter"].get("description") or ""
    assert "被遮蔽" in filt_desc
    assert "取自 search" in filt_desc
    sort_desc = props["sort"].get("description") or ""
    assert "被遮蔽" not in sort_desc


def test_10_fake_token_scan_zero_hits(
    spec_dir: Path, monkeypatch: pytest.MonkeyPatch
):
    """Fake-token full-schema scan: zero hits; curl guide keeps $TOKEN."""
    fake_token = "sk-probe-faketoken-ABCDEF1234567890"
    monkeypatch.setenv("GROUPED_PROBE_TOKEN", fake_token)
    ex = MCPExecutor(spec_dir, tool_mode="grouped",
                     server_override="http://127.0.0.1:1")
    blob = json.dumps(
        {n: s.input_schema for n, s in ex.tool_specs.items()},
        sort_keys=True,
        ensure_ascii=False,
        default=str,
    )
    assert fake_token not in blob
    assert not re.search(
        r"sk-[A-Za-z0-9_-]{8,}|ghp_[A-Za-z0-9]{8,}|Bearer [A-Za-z0-9]{12,}",
        blob,
    )
    file_desc = grouped_file_desc(ex, "docs", "file")
    assert "/upload" in file_desc
    assert "$TOKEN" in file_desc
    assert "不要直接填你机器的本地路径" in file_desc


def grouped_file_desc(
    ex: MCPExecutor, tool_name: str, field: str
) -> str:
    """File-field description of a grouped tool (curl-guide assertion helper)."""
    return (
        ex.tool_specs[tool_name].input_schema["properties"][field].get(
            "description"
        )
        or ""
    )

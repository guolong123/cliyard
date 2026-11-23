"""Shared fixtures for cliyard tests."""

from pathlib import Path

import pytest
import yaml

FIXTURES_DIR = Path(__file__).parent / "fixtures"


@pytest.fixture(autouse=True)
def isolate_serve_history_db(tmp_path, monkeypatch):
    """把 serve 历史库隔离到每个测试自己的 tmp 目录。

    默认路径 ``~/.cliyard/serve_history.db`` 在只读 HOME / 沙箱环境不可写，
    HistoryStore 初始化时会因无法建 WAL 文件而抛 ``OperationalError``，导致
    所有 ``create_app()`` 测试连带挂掉。这里统一覆盖 ``server_app._HISTORY_DB``，
    各测试内再自行 monkeypatch 的同名覆盖仍然优先（后设者胜）。
    """
    from cliyard.server import app as server_app

    monkeypatch.setattr(
        server_app, "_HISTORY_DB", str(tmp_path / "serve_history.db")
    )


@pytest.fixture
def fixtures_dir():
    """Path to the test fixtures directory."""
    return FIXTURES_DIR


@pytest.fixture
def minimal_service_path():
    """Path to minimal_service.yaml fixture."""
    return FIXTURES_DIR / "minimal_service.yaml"


@pytest.fixture
def repos_resource_path():
    """Path to repos_resource.yaml fixture."""
    return FIXTURES_DIR / "repos_resource.yaml"


@pytest.fixture
def bad_service_path():
    """Path to bad_service.yaml fixture."""
    return FIXTURES_DIR / "bad_service.yaml"


@pytest.fixture
def minimal_service_yaml(minimal_service_path):
    """Loaded minimal service YAML dict."""
    return yaml.safe_load(minimal_service_path.read_text())


@pytest.fixture
def repos_resource_yaml(repos_resource_path):
    """Loaded repos resource YAML dict."""
    return yaml.safe_load(repos_resource_path.read_text())


@pytest.fixture
def bad_service_yaml(bad_service_path):
    """Loaded bad service YAML dict."""
    return yaml.safe_load(bad_service_path.read_text())

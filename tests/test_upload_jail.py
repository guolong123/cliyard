"""Path-jail tests (plan mcp-file-client-origin todo 8).

Proves ``assert_server_readable`` gates every server-side file open:
outside-dir absolute paths raise ``CliyError`` (never leak content),
upload-dir and ``--file-allow-dirs`` files pass, and pipeline execution
plus sweep never delete caller inputs (never-delete-inputs proof).

Jail call sites (todo 5): ``engine/builder.py::execute_pipeline``
(non-multipart ``open()``) and ``engine/assembler.py::assemble_request``
(multipart ``open(rb)``), both active only under ``server_mode=True``
(MCP HTTP / serve submit chain); CLI and stdio pass ``False``.

No mocks of the unit under test: real ``assert_server_readable`` and
real ``execute_pipeline`` with minimal method_spec dicts; the only test
double is a downstream fake HTTP client (not the unit under test).

All fixtures live under per-test ``mkdtemp`` dirs (never shared temp
dirs — concurrent lanes), removed afterwards with removal asserted.
"""

from __future__ import annotations

import os
import shutil
import tempfile
import time

import pytest

from cliyard.engine.builder import ServiceContext, execute_pipeline
from cliyard.engine.errors import CliyError
from cliyard.server.uploads import (
    UPLOAD_TTL_S,
    assert_server_readable,
    save_upload,
    sweep,
)

OUTSIDE_MARKER = b"OUTSIDE-JAIL-CONTENT-9f3a2c1e"


@pytest.fixture()
def make_dir():
    """Per-test unique dirs under mkdtemp; removed afterwards (removal asserted)."""
    dirs: list[str] = []

    def _mk(prefix: str = "cliyard-jail-test-") -> str:
        d = tempfile.mkdtemp(prefix=prefix)
        dirs.append(d)
        return d

    yield _mk
    for d in dirs:
        shutil.rmtree(d, ignore_errors=True)
        assert not os.path.exists(d), f"temp dir not removed: {d}"


def _write(path: str, data: bytes) -> str:
    with open(path, "wb") as f:
        f.write(data)
    return path


def _file_method_spec() -> dict:
    """Minimal method_spec with one non-multipart ``type: file`` query param."""
    return {
        "http": {"method": "GET", "path": "items"},
        "params": {"query": [{"name": "doc", "type": "file"}]},
    }


class _FakeResponse:
    status_code = 200
    headers: dict = {}

    def json(self):  # noqa: ANN202
        return {}


class _FakeHttpClient:
    """Downstream HTTP double (not the unit under test)."""

    def __init__(self) -> None:
        self.default_headers: dict = {}
        self.seen: dict = {}

    def request(self, method=None, url=None, **kwargs):  # noqa: ANN001,ANN002,ANN202
        self.seen = {"method": method, "url": url, **kwargs}
        return _FakeResponse()


def _run_pipeline(path: str, upload_dir: str, allow_dirs=None, server_mode=True):
    """Run the real pipeline against a file param; return (result, client)."""
    client = _FakeHttpClient()
    result = execute_pipeline(
        {"doc": path},
        _file_method_spec(),
        {"path": "items"},
        ServiceContext(base_url="http://127.0.0.1:1"),
        resource_name="items",
        http_client=client,
        raw_response=True,
        server_mode=server_mode,
        upload_dir=upload_dir,
        allow_dirs=allow_dirs,
    )
    return result, client


# Direct helper: reject / allow -------------------------------------------


def test_jail_rejects_outside_absolute_path(make_dir) -> None:
    """Outside-dir absolute path → CliyError with re-upload guidance,
    leaking neither content nor the upload-dir truth."""
    upload_dir = make_dir()
    outside_dir = make_dir()
    outside = _write(os.path.join(outside_dir, "secret.txt"), OUTSIDE_MARKER)

    with pytest.raises(CliyError) as exc_info:
        assert_server_readable(outside, upload_dir)

    message = str(exc_info.value)
    assert OUTSIDE_MARKER.decode() not in message
    assert "POST /upload" in message
    assert os.path.realpath(upload_dir) not in message


def test_jail_rejects_missing_managed_path(make_dir) -> None:
    """In-jail but nonexistent path → CliyError (expired/missing), not FileNotFound."""
    upload_dir = make_dir()
    ghost = os.path.join(upload_dir, "cliyard-upload-deadbeef-gone.txt")

    with pytest.raises(CliyError) as exc_info:
        assert_server_readable(ghost, upload_dir)

    assert "POST /upload" in str(exc_info.value)
    assert not isinstance(exc_info.value, FileNotFoundError)


def test_jail_allows_upload_dir_file(make_dir) -> None:
    """File saved via the real store passes and resolves to its realpath."""
    upload_dir = make_dir()
    saved = save_upload("report.txt", b"managed bytes", upload_dir)

    assert assert_server_readable(saved["path"], upload_dir) == os.path.realpath(
        saved["path"]
    )


def test_jail_allows_allowlist_dir_list_and_pathsep(make_dir) -> None:
    """``--file-allow-dirs`` files pass (list form and os.pathsep string form)."""
    upload_dir = make_dir()
    allow_dir = make_dir()
    allowed = _write(os.path.join(allow_dir, "allowed.txt"), b"allowlisted bytes")

    assert assert_server_readable(allowed, upload_dir, allow_dirs=[allow_dir]) == (
        os.path.realpath(allowed)
    )
    assert assert_server_readable(
        allowed, upload_dir, allow_dirs=os.pathsep.join([allow_dir])
    ) == os.path.realpath(allowed)


def test_jail_allowlist_does_not_open_other_dirs(make_dir) -> None:
    """Allowlist is scoped: sibling outside dirs are still rejected."""
    upload_dir = make_dir()
    allow_dir = make_dir()
    other_dir = make_dir()
    other = _write(os.path.join(other_dir, "other.txt"), b"not allowlisted")

    with pytest.raises(CliyError):
        assert_server_readable(other, upload_dir, allow_dirs=[allow_dir])


# Pipeline level (server_mode=True seam) -----------------------------------


def test_pipeline_server_mode_rejects_outside_path(make_dir) -> None:
    """server_mode=True through the real pipeline: outside path → CliyError."""
    upload_dir = make_dir()
    outside_dir = make_dir()
    outside = _write(os.path.join(outside_dir, "secret.txt"), OUTSIDE_MARKER)

    with pytest.raises(CliyError) as exc_info:
        _run_pipeline(outside, upload_dir)

    assert OUTSIDE_MARKER.decode() not in str(exc_info.value)


def test_pipeline_server_mode_false_reads_outside_path(make_dir) -> None:
    """CLI/stdio对照: server_mode=False leaves local reads untouched."""
    upload_dir = make_dir()
    outside_dir = make_dir()
    outside = _write(os.path.join(outside_dir, "local.txt"), b"local content here")

    result, _ = _run_pipeline(outside, upload_dir, server_mode=False)

    assert result == {}


def test_pipeline_allows_upload_and_allowlist_files(make_dir) -> None:
    """Upload-dir and allowlist files flow through the pipeline; sources persist."""
    upload_dir = make_dir()
    allow_dir = make_dir()
    saved = save_upload("managed.txt", b"managed pipeline bytes", upload_dir)
    allowed = _write(os.path.join(allow_dir, "allowed.txt"), b"allowlisted bytes")

    assert _run_pipeline(saved["path"], upload_dir)[0] == {}
    assert _run_pipeline(allowed, upload_dir, allow_dirs=[allow_dir])[0] == {}

    assert os.path.isfile(saved["path"])
    assert os.path.isfile(allowed)


def test_pipeline_and_sweep_never_delete_inputs(make_dir) -> None:
    """Never-delete-inputs proof: allowlist source survives pipeline
    execution AND an all-expired sweep; only the managed-expired file goes."""
    upload_dir = make_dir()
    allow_dir = make_dir()
    now = time.time()

    allowed = _write(os.path.join(allow_dir, "source-input.txt"), b"caller input stays")
    os.utime(allowed, (now - UPLOAD_TTL_S - 600,) * 2)  # aged: guards alone save it
    managed = save_upload("doomed.txt", b"expired managed", upload_dir)
    os.utime(managed["path"], (now - UPLOAD_TTL_S - 600,) * 2)

    assert _run_pipeline(allowed, upload_dir, allow_dirs=[allow_dir])[0] == {}
    assert os.path.isfile(allowed), "pipeline must not consume caller inputs"

    result = sweep(upload_dir, now=now + UPLOAD_TTL_S + 1200)

    assert result["removed"] == 1
    assert not os.path.exists(managed["path"])
    assert os.path.isfile(allowed)
    with open(allowed, "rb") as f:
        assert f.read() == b"caller input stays"


def test_bypass_accepts_dot_slash_variant(make_dir) -> None:
    """Non-normalized variant of a bridged temp path passes the bypass (no false jail)."""
    from cliyard.server.uploads import normalize_bypass

    upload_dir = make_dir()
    bridge_dir = make_dir()
    bridged = _write(
        os.path.join(bridge_dir, "cliyard-upload-abc12345-bridge.bin"),
        b"bridge bytes",
    )
    variant = "./" + os.path.relpath(bridged)
    assert variant != bridged
    assert os.path.realpath(variant) == os.path.realpath(bridged)
    # The old string-compare misses it (the PR-review false-jail finding)...
    assert variant not in set([bridged])
    # ...the normalized bypass accepts it, in both spellings.
    assert variant in normalize_bypass([bridged])
    assert bridged in normalize_bypass([bridged])
    # Fail-closed: unknown paths still miss; empty/None inputs never raise.
    assert "no-such-file-anywhere" not in normalize_bypass([bridged])
    assert normalize_bypass(None) == set()
    assert normalize_bypass([]) == set()
    assert normalize_bypass(["", None]) == set()


def test_pipeline_bypass_accepts_dot_slash_variant(make_dir) -> None:
    """End-to-end: a ./-variant of a bridge output flows through server_mode."""
    upload_dir = make_dir()
    bridge_dir = make_dir()
    bridged = _write(
        os.path.join(bridge_dir, "cliyard-upload-abc12345-bridge.bin"),
        b"bridge pipeline bytes",
    )
    variant = "./" + os.path.relpath(bridged)

    client = _FakeHttpClient()
    result = execute_pipeline(
        {"doc": variant},
        _file_method_spec(),
        {"path": "items"},
        ServiceContext(base_url="http://127.0.0.1:1"),
        resource_name="items",
        http_client=client,
        raw_response=True,
        server_mode=True,
        upload_dir=upload_dir,
        server_tmp_files=[bridged],
    )
    assert result == {}

"""Sweep safety tests (plan mcp-file-client-origin todo 8).

Proves the guarded-sweep invariant of ``cliyard.server.uploads.sweep``:
deletion primitives never accept caller paths — only prefix-matched,
in-directory regular files are ever unlinked.

Guard mapping (``src/cliyard/server/uploads.py::sweep``):
- prefix gate  ``entry.name.startswith(UPLOAD_FILENAME_PREFIX)`` → foreign
  no-prefix files are never classified as managed;
- symlink skip ``entry.is_symlink()`` → symlinks (outside targets, loops)
  are unlinked-never, followed-never;
- subdir skip  ``entry.is_file(follow_symlinks=False)`` → nested dirs/files
  are never descended into (single non-recursive ``os.scandir`` pass);
- unlink recheck ``os.path.realpath(candidate)`` dirname == resolved dir
  → TOCTOU symlink-swap before ``os.unlink`` aborts the delete.

Failing-first note: the foreign-survival tests below deliberately age the
foreign items past ``UPLOAD_TTL_S``. With the prefix/symlink/subdir guards
in place they survive untouched; were any guard removed, the aged foreign
items would be classified as managed-expired and deleted, failing the
byte-identical assertions. (No product edits were made to prove this —
the observable guard behavior is asserted directly: presence + content +
mtime unchanged.)

All fixtures live under per-test ``mkdtemp`` dirs (never shared temp
dirs — concurrent lanes), removed afterwards with removal asserted.
Time is injected via ``sweep(now=...)`` — never ``sleep``.
"""

from __future__ import annotations

import os
import shutil
import tempfile
import time

import pytest

from cliyard.server import uploads as uploads_mod
from cliyard.server.uploads import UPLOAD_FILENAME_PREFIX, UPLOAD_TTL_S, save_upload, sweep


@pytest.fixture()
def make_dir():
    """Per-test unique dirs under mkdtemp; removed afterwards (removal asserted)."""
    dirs: list[str] = []

    def _mk(prefix: str = "cliyard-sweep-test-") -> str:
        d = tempfile.mkdtemp(prefix=prefix)
        dirs.append(d)
        return d

    yield _mk
    for d in dirs:
        shutil.rmtree(d, ignore_errors=True)
        assert not os.path.exists(d), f"temp dir not removed: {d}"


def _seed_foreign(upload_dir: str, outside_dir: str) -> dict[str, object]:
    """Pre-seed foreign items; return paths/contents/mtime for intactness checks."""
    # 1. No-prefix important file — aged past TTL so a missing prefix-guard
    #    would classify it as managed-expired and delete it.
    important = os.path.join(upload_dir, "important-do-not-delete.txt")
    contents_important = b"critical user data - must survive sweep"
    with open(important, "wb") as f:
        f.write(contents_important)

    # 2. Nested subdir with a file inside (also prefix-less, also aged).
    subdir = os.path.join(upload_dir, "subdir")
    os.makedirs(subdir)
    nested = os.path.join(subdir, "nested.txt")
    contents_nested = b"nested content stays"
    with open(nested, "wb") as f:
        f.write(contents_nested)

    # 3. Symlink to an outside file — sweep must neither follow nor unlink.
    outside_target = os.path.join(outside_dir, "outside-secret.txt")
    contents_outside = b"outside bytes - sweep must not touch"
    with open(outside_target, "wb") as f:
        f.write(contents_outside)
    os.symlink(outside_target, os.path.join(upload_dir, "link-to-outside"))

    # 4. Symlink loop (points at the containing dir itself) — proves the
    #    non-recursive scan cannot hang or descend; skipped as non-regular.
    os.symlink(upload_dir, os.path.join(upload_dir, "loopdir"))

    # Age foreign regular files past expiry: only the guards keep them alive.
    old = time.time() - UPLOAD_TTL_S - 300
    os.utime(important, (old, old))
    os.utime(nested, (old, old))
    return {
        "important": important,
        "nested": nested,
        "outside_target": outside_target,
        "contents_important": contents_important,
        "contents_nested": contents_nested,
        "contents_outside": contents_outside,
        "old_mtime": old,
    }


def _assert_foreign_intact(upload_dir: str, seed: dict[str, object]) -> None:
    """All foreign items present, byte-identical, mtimes unchanged."""
    old = float(seed["old_mtime"])  # type: ignore[arg-type]
    important = str(seed["important"])
    nested = str(seed["nested"])
    outside_target = str(seed["outside_target"])

    assert os.path.isfile(important)
    with open(important, "rb") as f:
        assert f.read() == seed["contents_important"]
    assert os.stat(important).st_mtime == pytest.approx(old, abs=1.0)

    assert os.path.isfile(nested)
    with open(nested, "rb") as f:
        assert f.read() == seed["contents_nested"]
    assert os.stat(nested).st_mtime == pytest.approx(old, abs=1.0)

    link = os.path.join(upload_dir, "link-to-outside")
    assert os.path.islink(link), "symlink to outside must not be unlinked"
    assert os.readlink(link) == outside_target
    with open(outside_target, "rb") as f:
        assert f.read() == seed["contents_outside"]

    loop = os.path.join(upload_dir, "loopdir")
    assert os.path.islink(loop), "symlink loop must survive sweep"


def test_sweep_preserves_foreign_items(make_dir) -> None:
    """Aged foreign items + symlinks + subdir survive sweep byte-identical."""
    upload_dir = make_dir()
    outside_dir = make_dir()
    seed = _seed_foreign(upload_dir, outside_dir)

    result = sweep(upload_dir)

    assert result["removed"] == 0
    _assert_foreign_intact(upload_dir, seed)


def test_sweep_removes_expired_keeps_fresh(make_dir) -> None:
    """Expired managed upload gone; fresh managed upload kept."""
    upload_dir = make_dir()
    now = time.time()

    expired = save_upload("old.txt", b"stale bytes", upload_dir)
    fresh = save_upload("new.txt", b"fresh bytes", upload_dir)
    os.utime(expired["path"], (now - UPLOAD_TTL_S - 120,) * 2)
    os.utime(fresh["path"], (now - 60,) * 2)

    result = sweep(upload_dir, now=now)

    assert result["removed"] == 1
    assert result["kept"] == 1
    assert not os.path.exists(expired["path"])
    assert os.path.isfile(fresh["path"])
    with open(fresh["path"], "rb") as f:
        assert f.read() == b"fresh bytes"


def test_sweep_quota_evicts_oldest_first_by_count(make_dir, monkeypatch) -> None:
    """Over-quota (file count): oldest managed uploads evicted first, order asserted."""
    upload_dir = make_dir()
    monkeypatch.setattr(uploads_mod, "UPLOAD_QUOTA_FILES", 3)
    now = time.time()

    paths: list[tuple[float, str]] = []
    for i in range(5):
        saved = save_upload(f"f{i}.txt", b"x", upload_dir)
        mtime = now - (500 - i * 100)  # f0 oldest … f4 newest, all fresh
        os.utime(saved["path"], (mtime, mtime))
        paths.append((mtime, saved["path"]))
    paths.sort()

    result = sweep(upload_dir, now=now)

    assert result["removed"] == 2
    assert result["kept"] == 3
    # Oldest-first: the two oldest are gone, the three newest remain.
    for _, p in paths[:2]:
        assert not os.path.exists(p), f"oldest should be evicted: {p}"
    for _, p in paths[2:]:
        assert os.path.isfile(p), f"newest should be kept: {p}"


def test_sweep_quota_evicts_oldest_first_by_bytes(make_dir, monkeypatch) -> None:
    """Over-quota (total bytes): oldest-first until under budget."""
    upload_dir = make_dir()
    monkeypatch.setattr(uploads_mod, "UPLOAD_QUOTA_FILES", 1000)
    monkeypatch.setattr(uploads_mod, "UPLOAD_QUOTA_BYTES", 100)
    now = time.time()

    paths: list[tuple[float, str]] = []
    for i in range(5):
        saved = save_upload(f"b{i}.bin", b"x" * 30, upload_dir)
        mtime = now - (500 - i * 100)
        os.utime(saved["path"], (mtime, mtime))
        paths.append((mtime, saved["path"]))
    paths.sort()

    result = sweep(upload_dir, now=now)

    # 150 bytes total; evict oldest 30 → 120, oldest 30 → 90 ≤ 100, stop.
    assert result["removed"] == 2
    assert result["freed_bytes"] == 60
    assert result["kept"] == 3
    for _, p in paths[:2]:
        assert not os.path.exists(p)
    for _, p in paths[2:]:
        assert os.path.isfile(p)


def test_sweep_dirty_dir_regression(make_dir, monkeypatch) -> None:
    """Dirty dir (foreign files present) + quota overflow: zero foreign loss,
    quota evicts only managed-oldest."""
    upload_dir = make_dir()
    outside_dir = make_dir()
    seed = _seed_foreign(upload_dir, outside_dir)
    monkeypatch.setattr(uploads_mod, "UPLOAD_QUOTA_FILES", 2)
    now = time.time()

    managed: list[tuple[float, str]] = []
    for i in range(4):
        saved = save_upload(f"m{i}.txt", b"managed", upload_dir)
        mtime = now - (400 - i * 100)
        os.utime(saved["path"], (mtime, mtime))
        managed.append((mtime, saved["path"]))
    managed.sort()

    result = sweep(upload_dir, now=now)

    assert result["removed"] == 2
    _assert_foreign_intact(upload_dir, seed)
    for _, p in managed[:2]:
        assert not os.path.exists(p)
    for _, p in managed[2:]:
        assert os.path.isfile(p)


def test_sweep_prefix_gate_only(make_dir) -> None:
    """Files without the managed prefix are never classified — even when
    their names merely resemble managed ones."""
    upload_dir = make_dir()
    now = time.time()

    lookalike = os.path.join(upload_dir, "cliyard-upload")
    with open(lookalike, "wb") as f:
        f.write(b"prefix-less lookalike")
    os.utime(lookalike, (now - UPLOAD_TTL_S - 600,) * 2)

    managed = save_upload("real.txt", b"managed", upload_dir)
    assert os.path.basename(managed["path"]).startswith(UPLOAD_FILENAME_PREFIX)
    os.utime(managed["path"], (now - UPLOAD_TTL_S - 600,) * 2)

    result = sweep(upload_dir, now=now)

    assert result["removed"] == 1
    assert os.path.isfile(lookalike)
    assert not os.path.exists(managed["path"])


def test_sweep_prefix_symlink_survives_race(make_dir, monkeypatch) -> None:
    """Prefix-named symlink + mid-sweep TOCTOU swap: outside target never harmed.

    Covers the PR-review TOCTOU finding on the sweep deletion path: a managed
    regular file swapped for an outside-pointing symlink between scan and
    unlink must not cause any outside delete (``O_NOFOLLOW`` + ``dir_fd``
    unlink never follows the final component); the pre-existing prefix-named
    symlink is skipped by the scan and survives byte-identical.
    """
    upload_dir = make_dir()
    outside_dir = make_dir()
    now = time.time()
    old = now - UPLOAD_TTL_S - 300

    secret = os.path.join(outside_dir, "race-secret.txt")
    secret_bytes = b"race-secret-must-survive-4d2e"
    with open(secret, "wb") as f:
        f.write(secret_bytes)

    # 1. Prefix-named symlink already sitting in the dir (aged past TTL).
    link_name = f"{UPLOAD_FILENAME_PREFIX}deadbeef-raced.txt"
    link_path = os.path.join(upload_dir, link_name)
    os.symlink(secret, link_path)

    # 2. Real expired managed file; a racer swaps it for an outside symlink
    #    inside os.unlink (i.e. between _unlink's checks and the delete).
    victim = save_upload("victim.txt", b"victim bytes", upload_dir)
    os.utime(victim["path"], (old, old))

    real_unlink = os.unlink
    swapped = {"done": False}

    def _racy_unlink(path, *, dir_fd=None):
        if dir_fd is not None and not swapped["done"]:
            swapped["done"] = True
            real_unlink(victim["path"])
            os.symlink(secret, victim["path"])
        return real_unlink(path, dir_fd=dir_fd)

    monkeypatch.setattr(os, "unlink", _racy_unlink)
    result = sweep(upload_dir, now=now)

    assert swapped["done"], "race hook must have fired"
    with open(secret, "rb") as f:
        assert f.read() == secret_bytes
    assert os.path.islink(link_path), "prefix symlink must survive sweep"
    assert os.readlink(link_path) == secret
    assert result["removed"] >= 1

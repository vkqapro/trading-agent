from __future__ import annotations

import json
import os

from src.crypto import worker


def test_pid_alive_recognizes_current_process():
    assert worker._pid_alive(os.getpid()) is True


def test_crypto_lock_refuses_when_owner_pid_is_alive(tmp_path, monkeypatch):
    lock_path = tmp_path / "worker.lock"
    heartbeat_path = tmp_path / "worker.json"
    lock_path.write_text(json.dumps({"pid": 1234}), encoding="utf-8")
    old = 1
    os.utime(lock_path, (old, old))
    monkeypatch.setattr(worker, "LOCK_PATH", lock_path)
    monkeypatch.setattr(worker, "HEARTBEAT_PATH", heartbeat_path)
    monkeypatch.setattr(worker, "ensure_crypto_directories", lambda: None)
    monkeypatch.setattr(worker, "_pid_alive", lambda pid: int(pid) == 1234)

    with worker._single_worker_lock(stale_after_seconds=1) as acquired:
        assert acquired is False

    assert json.loads(lock_path.read_text(encoding="utf-8"))["pid"] == 1234


def test_crypto_lock_recovers_stale_dead_owner(tmp_path, monkeypatch):
    lock_path = tmp_path / "worker.lock"
    heartbeat_path = tmp_path / "worker.json"
    lock_path.write_text(json.dumps({"pid": 999999}), encoding="utf-8")
    old = 1
    os.utime(lock_path, (old, old))
    monkeypatch.setattr(worker, "LOCK_PATH", lock_path)
    monkeypatch.setattr(worker, "HEARTBEAT_PATH", heartbeat_path)
    monkeypatch.setattr(worker, "ensure_crypto_directories", lambda: None)
    monkeypatch.setattr(worker, "_pid_alive", lambda pid: False)

    with worker._single_worker_lock(stale_after_seconds=1) as acquired:
        assert acquired is True
        assert json.loads(lock_path.read_text(encoding="utf-8"))["pid"] == os.getpid()

    assert not lock_path.exists()


def test_crypto_lock_release_preserves_new_owner(tmp_path, monkeypatch):
    lock_path = tmp_path / "worker.lock"
    heartbeat_path = tmp_path / "worker.json"
    monkeypatch.setattr(worker, "LOCK_PATH", lock_path)
    monkeypatch.setattr(worker, "HEARTBEAT_PATH", heartbeat_path)
    monkeypatch.setattr(worker, "ensure_crypto_directories", lambda: None)

    with worker._single_worker_lock(stale_after_seconds=1) as acquired:
        assert acquired is True
        lock_path.write_text(json.dumps({"pid": os.getpid() + 1}), encoding="utf-8")

    assert lock_path.exists()
    assert json.loads(lock_path.read_text(encoding="utf-8"))["pid"] == os.getpid() + 1

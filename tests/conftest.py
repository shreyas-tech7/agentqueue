"""Test guards.

1. Any socket connection to a host that is not local fails the test.
2. Every test runs inside its own temp directory.
"""

import socket
import sys
from pathlib import Path

import pytest

sys.path.insert(0, str(Path(__file__).parent))

LOCAL = {"localhost", "127.0.0.1", "::1"}
_real_connect = socket.socket.connect
_real_connect_ex = socket.socket.connect_ex


class NetworkBlocked(RuntimeError):
    pass


def _check(self, address):
    host = address[0] if isinstance(address, tuple) else address
    if self.family != socket.AF_UNIX and host not in LOCAL:
        raise NetworkBlocked(f"test tried to reach {host!r}; tests must run offline")


def _guarded_connect(self, address):
    _check(self, address)
    return _real_connect(self, address)


def _guarded_connect_ex(self, address):
    _check(self, address)
    return _real_connect_ex(self, address)


@pytest.fixture(autouse=True)
def _no_network(monkeypatch):
    monkeypatch.setattr(socket.socket, "connect", _guarded_connect)
    monkeypatch.setattr(socket.socket, "connect_ex", _guarded_connect_ex)
    yield


@pytest.fixture(autouse=True)
def _in_temp_dir(tmp_path, monkeypatch):
    monkeypatch.chdir(tmp_path)
    yield

"""Shared test fixtures, including an outbound-network safety guard."""

from urllib.parse import urlsplit

import pytest
import requests
from urllib3.connection import HTTPConnection, HTTPSConnection
from urllib3.connectionpool import HTTPConnectionPool, HTTPSConnectionPool

_LOCAL_HOSTS = {"localhost", "127.0.0.1", "::1"}


def _is_local(host: str | None) -> bool:
    return host is not None and host.strip("[]").casefold() in _LOCAL_HOSTS


def _guarded_pool_new_conn(original_new_conn):
    """Wrap one pool connection creator while retaining its original method."""

    def guarded_new_conn(pool, *args, **kwargs):
        if not _is_local(pool.host):
            raise AssertionError(
                f"External network access is blocked in tests: {pool.host}"
            )
        return original_new_conn(pool, *args, **kwargs)

    return guarded_new_conn


def _guarded_connection_new_conn(original_new_conn):
    """Wrap a connection socket creator while retaining its original method."""

    def guarded_new_conn(connection, *args, **kwargs):
        if not _is_local(connection.host):
            raise AssertionError(
                f"External network access is blocked in tests: {connection.host}"
            )
        return original_new_conn(connection, *args, **kwargs)

    return guarded_new_conn


@pytest.fixture(autouse=True)
def forbid_external_network(monkeypatch):
    """Reject requests and urllib3 socket creation to non-local hosts."""
    original_request = requests.Session.request

    def guarded_request(self, method, url, *args, **kwargs):
        if not _is_local(urlsplit(url).hostname):
            raise AssertionError(f"External network access is blocked in tests: {url}")
        return original_request(self, method, url, *args, **kwargs)

    monkeypatch.setattr(requests.Session, "request", guarded_request)
    for pool_class in (HTTPConnectionPool, HTTPSConnectionPool):
        original_new_conn = pool_class._new_conn
        monkeypatch.setattr(
            pool_class, "_new_conn", _guarded_pool_new_conn(original_new_conn)
        )
    for connection_class in (HTTPConnection, HTTPSConnection):
        original_new_conn = connection_class._new_conn
        monkeypatch.setattr(
            connection_class,
            "_new_conn",
            _guarded_connection_new_conn(original_new_conn),
        )

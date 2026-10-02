import socket

import pytest

PUBLIC_IP = "93.184.216.34"


@pytest.fixture(autouse=True)
def _stub_dns(monkeypatch):
    def fake_getaddrinfo(host, port, *args, **kwargs):
        if isinstance(host, str) and (host == "testserver" or host.endswith(".test")):
            return [(socket.AF_INET, socket.SOCK_STREAM, 6, "", (PUBLIC_IP, 0))]
        raise socket.gaierror(-2, "Name or service not known")

    monkeypatch.setattr("federation.client.getaddrinfo", fake_getaddrinfo)

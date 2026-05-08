from __future__ import annotations

import socket
from dataclasses import dataclass
from typing import Any
from urllib.parse import urlparse


class RuntimeDependencyError(RuntimeError):
    pass


@dataclass(frozen=True)
class DependencyCheck:
    name: str
    ok: bool
    target: str
    error: str | None = None

    def as_dict(self) -> dict[str, Any]:
        return {
            "name": self.name,
            "ok": self.ok,
            "target": self.target,
            "error": self.error,
        }


def _host_port_from_url(url: str, default_port: int) -> tuple[str, int]:
    parsed = urlparse(url)
    if not parsed.hostname:
        raise ValueError(f"Invalid service URL: {url}")
    host = "127.0.0.1" if parsed.hostname == "localhost" else parsed.hostname
    return host, parsed.port or default_port


def _tcp_check(name: str, url: str, default_port: int, timeout: float) -> DependencyCheck:
    try:
        host, port = _host_port_from_url(url, default_port)
        with socket.create_connection((host, port), timeout=timeout):
            return DependencyCheck(name=name, ok=True, target=f"{host}:{port}")
    except OSError as exc:
        target = url
        try:
            host, port = _host_port_from_url(url, default_port)
            target = f"{host}:{port}"
        except ValueError:
            pass
        return DependencyCheck(name=name, ok=False, target=target, error=str(exc))
    except ValueError as exc:
        return DependencyCheck(name=name, ok=False, target=url, error=str(exc))


def _redis_ping(url: str, timeout: float) -> DependencyCheck:
    try:
        host, port = _host_port_from_url(url, 6379)
        with socket.create_connection((host, port), timeout=timeout) as sock:
            sock.settimeout(timeout)
            parsed = urlparse(url)
            password = parsed.password
            username = parsed.username
            if password:
                if username:
                    command = (
                        f"*3\r\n$4\r\nAUTH\r\n${len(username)}\r\n{username}\r\n"
                        f"${len(password)}\r\n{password}\r\n"
                    )
                else:
                    command = f"*2\r\n$4\r\nAUTH\r\n${len(password)}\r\n{password}\r\n"
                sock.sendall(command.encode("utf-8"))
                auth_response = sock.recv(128)
                if not auth_response.startswith(b"+OK"):
                    raise OSError(auth_response.decode("utf-8", errors="replace").strip())

            sock.sendall(b"*1\r\n$4\r\nPING\r\n")
            response = sock.recv(128)
            if not response.startswith(b"+PONG"):
                raise OSError(response.decode("utf-8", errors="replace").strip())
            return DependencyCheck(name="redis", ok=True, target=f"{host}:{port}")
    except OSError as exc:
        target = url
        try:
            host, port = _host_port_from_url(url, 6379)
            target = f"{host}:{port}"
        except ValueError:
            pass
        return DependencyCheck(name="redis", ok=False, target=target, error=str(exc))
    except ValueError as exc:
        return DependencyCheck(name="redis", ok=False, target=url, error=str(exc))


def check_runtime_services(settings: Any, raise_on_error: bool = True) -> list[DependencyCheck]:
    checks = [
        _tcp_check(
            "postgresql",
            settings.database_url,
            default_port=5432,
            timeout=settings.runtime_check_timeout_seconds,
        ),
        _redis_ping(settings.redis_url, timeout=settings.runtime_check_timeout_seconds),
    ]

    failed = [item for item in checks if not item.ok]
    if failed and raise_on_error:
        details = "; ".join(f"{item.name} at {item.target}: {item.error}" for item in failed)
        raise RuntimeDependencyError(
            "TravelShot Agent requires PostgreSQL and Redis to be running before the API starts. "
            f"Start them with `docker compose up postgres redis` or `docker compose up`, then retry. "
            f"Failed checks: {details}"
        )

    return checks

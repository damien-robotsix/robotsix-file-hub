"""Guard against dev-stack config drift in the repo-root docker-compose.yml.

The compose file mounts a JSON config into the backend container as
``ROBOTSIX_CONFIG_FILE``. That config points at other services by hostname
(``postgres``, ``minio``, ``ollama``, ...). If it names a host:port that no
compose service provides — or, worse, ``localhost`` (which inside a container
cannot reach a sibling service or the host) — ``docker compose up`` yields a
backend that boots and passes its health check yet cannot store files or
generate embeddings.

This test asserts that every host:port target in the compose-mounted config
resolves to a service defined in the same compose file, so that drift fails
CI instead of silently shipping a broken local stack.
"""

from __future__ import annotations

import json
from pathlib import Path
from typing import Any
from urllib.parse import urlsplit

import yaml  # type: ignore[import-untyped]

REPO_ROOT = Path(__file__).resolve().parents[2]
COMPOSE_FILE = REPO_ROOT / "docker-compose.yml"

# Env var whose value is the in-container path of the mounted config file.
CONFIG_ENV_VAR = "ROBOTSIX_CONFIG_FILE"


def _load_yaml(path: Path) -> dict[str, Any]:
    with path.open(encoding="utf-8") as fh:
        data = yaml.safe_load(fh)
    assert isinstance(data, dict), f"{path} did not parse to a mapping"
    return data


def _env_mapping(service: dict[str, Any]) -> dict[str, str]:
    """Return a service's ``environment`` as a dict, accepting both forms.

    Compose accepts ``{KEY: value}`` mappings and ``["KEY=value"]`` lists.
    """
    env = service.get("environment", {})
    if isinstance(env, dict):
        return {str(k): str(v) for k, v in env.items()}
    result: dict[str, str] = {}
    for item in env:
        key, _, value = str(item).partition("=")
        result[key] = value
    return result


def _mounted_config_path(service: dict[str, Any], container_path: str) -> Path | None:
    """Resolve the host path bind-mounted at ``container_path`` for a service."""
    for volume in service.get("volumes", []):
        if isinstance(volume, str):
            parts = volume.split(":")
            if len(parts) >= 2:
                source, target = parts[0], parts[1]
            else:
                continue
        elif isinstance(volume, dict):
            source, target = volume.get("source", ""), volume.get("target", "")
        else:
            continue
        if target == container_path:
            return (REPO_ROOT / source).resolve()
    return None


def _host_port_targets(value: Any) -> set[str]:
    """Collect hostnames from every string in ``value`` that carries a port.

    A "host:port target" is any URL with an explicit port (e.g.
    ``http://minio:9000`` or ``postgresql+asyncpg://user@postgres:5432/db``).
    URLs without a port (external hosted APIs like ``https://host.example``)
    are intentionally ignored — they are not part of the compose stack.
    """
    hosts: set[str] = set()
    if isinstance(value, str):
        split = urlsplit(value)
        if split.port is not None and split.hostname:
            hosts.add(split.hostname)
    elif isinstance(value, dict):
        for item in value.values():
            hosts |= _host_port_targets(item)
    elif isinstance(value, list):
        for item in value:
            hosts |= _host_port_targets(item)
    return hosts


def test_compose_config_hosts_resolve_to_services() -> None:
    compose = _load_yaml(COMPOSE_FILE)
    services = compose.get("services", {})
    assert services, "docker-compose.yml defines no services"
    service_names = set(services)

    checked_any = False
    for name, service in services.items():
        env = _env_mapping(service)
        container_path = env.get(CONFIG_ENV_VAR)
        if not container_path:
            continue

        config_path = _mounted_config_path(service, container_path)
        assert config_path is not None, (
            f"service '{name}' sets {CONFIG_ENV_VAR}={container_path} but mounts "
            f"no volume at that path"
        )
        assert config_path.is_file(), (
            f"service '{name}' mounts {config_path} as its config, but that file does not exist"
        )

        config = json.loads(config_path.read_text(encoding="utf-8"))
        hosts = _host_port_targets(config)
        unresolved = sorted(h for h in hosts if h not in service_names)
        assert not unresolved, (
            f"service '{name}' config {config_path.name} targets host(s) "
            f"{unresolved} with a port, but no matching service exists in "
            f"{COMPOSE_FILE.name} (services: {sorted(service_names)}). "
            f"Either add the service or point the config at an existing one."
        )
        checked_any = True

    assert checked_any, (
        f"no compose service sets {CONFIG_ENV_VAR}; expected the backend to "
        f"mount a config file — did the compose layout change?"
    )

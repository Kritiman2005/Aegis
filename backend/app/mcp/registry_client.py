"""
Aegis — MCP Registry Search Client

Searches the official public MCP Registry (registry.modelcontextprotocol.io)
— the same community index FLUJO's registryClient.ts/MarketplaceTab query —
so a user can find a server by name/description instead of already knowing
its npm/pypi package or GitHub repo. Read-only and unauthenticated: this
only ever GETs the registry's search endpoint, nothing is installed just
from a search.

Verified live against the real API (2026-09):
  GET https://registry.modelcontextprotocol.io/v0/servers?search=<q>&limit=<n>
  -> {"servers": [{"server": {name, description, version, repository,
       packages: [...], remotes: [...]}, "_meta": {...}}, ...], "metadata": {...}}

Each result's `packages` (installed as a local stdio subprocess) or
`remotes` (a hosted streamable-http/SSE endpoint) is reduced down to a
single best install suggestion Aegis can act on directly — see
_extract_install. Anything neither covered (docker-only packages, an
unrecognized registry_type) comes back with install=None; the frontend
falls back to "open the repo and configure it as a custom server"
rather than guessing wrong.
"""

import logging
from typing import Any, Dict, List, Optional

import httpx

logger = logging.getLogger(__name__)

REGISTRY_BASE_URL = "https://registry.modelcontextprotocol.io"
_TIMEOUT = httpx.Timeout(15.0)

# registryType -> (runtime command prefix, package-manager-provided auto-install)
_RUNTIME_BY_REGISTRY_TYPE = {
    "npm": ["npx", "-y"],
    "pypi": ["uvx"],
}


def _extract_env_schema(package: Dict[str, Any]) -> List[Dict[str, Any]]:
    fields = []
    for ev in package.get("environmentVariables") or []:
        name = ev.get("name")
        if not name:
            continue
        fields.append({
            "key": name,
            "label": ev.get("description") or name,
            "required": bool(ev.get("isRequired")),
            "secret": bool(ev.get("isSecret")),
            "default": ev.get("default"),
        })
    return fields


def _extract_install(server: Dict[str, Any]) -> Optional[Dict[str, Any]]:
    """
    Reduces one registry server entry into a single installable suggestion:
      - a hosted "remote" entry -> {"kind": "remote", "url", "headers": [...]}
        (headers list mirrors env fields: name/description/isRequired/isSecret
        — a static value the user pastes in, never an OAuth exchange)
      - an npm/pypi package -> {"kind": "stdio", "command": [...], "env_schema": [...]}
    Returns None when nothing here is something Aegis knows how to run
    automatically (e.g. docker-only, an unrecognized package registry) —
    callers should point the user at the repo instead of guessing.
    """
    remotes = server.get("remotes") or []
    for r in remotes:
        url = r.get("url")
        if not url:
            continue
        headers = []
        for h in r.get("headers") or []:
            name = h.get("name")
            if not name:
                continue
            headers.append({
                "key": name,
                "label": h.get("description") or name,
                "required": bool(h.get("isRequired")),
                "secret": bool(h.get("isSecret")),
                "value_template": h.get("value"),
            })
        return {"kind": "remote", "url": url, "transport": r.get("type"), "headers": headers}

    packages = server.get("packages") or []
    for p in packages:
        reg_type = (p.get("registryType") or p.get("registry_type") or "").lower()
        prefix = _RUNTIME_BY_REGISTRY_TYPE.get(reg_type)
        identifier = p.get("identifier") or p.get("name")
        if not prefix or not identifier:
            continue
        version = p.get("version")
        pkg_ref = f"{identifier}@{version}" if version else identifier
        return {
            "kind": "stdio",
            "command": [*prefix, pkg_ref],
            "env_schema": _extract_env_schema(p),
        }

    return None


def search(query: str, limit: int = 20) -> List[Dict[str, Any]]:
    """
    Live-searches the public MCP registry. Raises RuntimeError on network/
    HTTP failure — callers surface that as a real error (502) rather than
    silently reporting zero results.
    """
    if not query or not query.strip():
        return []

    try:
        with httpx.Client(timeout=_TIMEOUT) as client:
            resp = client.get(
                f"{REGISTRY_BASE_URL}/v0/servers",
                params={"search": query.strip(), "limit": limit},
                headers={"Accept": "application/json"},
            )
            resp.raise_for_status()
            data = resp.json()
    except httpx.HTTPError as e:
        logger.error(f"MCP registry search failed for query '{query}': {e}")
        raise RuntimeError(f"Could not reach the MCP registry: {e}") from e

    entries = data.get("servers") or []
    results = []
    for entry in entries:
        server = entry.get("server") or entry
        name = server.get("name")
        if not name:
            continue
        results.append({
            "name": name,
            "description": server.get("description"),
            "version": server.get("version"),
            "repository_url": (server.get("repository") or {}).get("url"),
            "install": _extract_install(server),
        })
    return results

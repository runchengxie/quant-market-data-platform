"""Environment and credential resolution for the TuShare A-share provider."""

from __future__ import annotations

import importlib
import os
import re
from pathlib import Path

from market_data_platform.providers.tushare_a_share_options import DEFAULT_API_URL_ENV


def _env_file_candidates() -> tuple[Path, ...]:
    roots = [Path.cwd(), Path(__file__).resolve().parents[3]]
    paths: list[Path] = []
    seen: set[Path] = set()
    for root in roots:
        for name in (".env.local", ".env"):
            path = (root / name).expanduser().resolve()
            if path not in seen:
                paths.append(path)
                seen.add(path)
    stable_path = Path.home() / ".config" / "market-data-platform" / "config.env"
    stable_path = stable_path.expanduser().resolve()
    if stable_path not in seen:
        paths.append(stable_path)
    return tuple(paths)


def _parse_simple_env_file(path: Path) -> dict[str, str]:
    values: dict[str, str] = {}
    for raw_line in path.read_text(encoding="utf-8").splitlines():
        line = raw_line.strip()
        if not line or line.startswith("#"):
            continue
        if line.startswith("export "):
            line = line.removeprefix("export ").lstrip()
        if "=" not in line:
            continue
        key, value = line.split("=", 1)
        key = key.strip()
        if not re.fullmatch(r"[A-Za-z_][A-Za-z0-9_]*", key):
            continue
        value = value.strip()
        if value[:1] not in {"'", '"'} and " #" in value:
            value = value.split(" #", 1)[0].strip()
        if len(value) >= 2 and value[0] == value[-1] and value[0] in {"'", '"'}:
            value = value[1:-1]
        values[key] = value
    return values


def _load_env_file(path: Path) -> bool:
    if not path.is_file():
        return False
    try:
        dotenv = importlib.import_module("dotenv")
    except ImportError:
        values = _parse_simple_env_file(path)
    else:
        values = {
            str(key): str(value)
            for key, value in dotenv.dotenv_values(path).items()
            if key and value is not None
        }
    for key, value in values.items():
        os.environ.setdefault(key, value)
    return True


def _load_tushare_env_files() -> tuple[str, ...]:
    loaded: list[str] = []
    for path in _env_file_candidates():
        if _load_env_file(path):
            loaded.append(str(path))
    return tuple(loaded)


def _resolve_token(token: str | None, token_env: str) -> str:
    _load_tushare_env_files()
    value = str(token or os.environ.get(token_env) or "").strip()
    if not value:
        raise RuntimeError(f"No TuShare token found in environment variable {token_env}.")
    return value


def _api_url_env_candidates(token_env: str) -> tuple[str, ...]:
    token_key = str(token_env or "").strip()
    candidates: list[str] = []
    match = re.fullmatch(r"TUSHARE_TOKEN(_[A-Za-z0-9]+)?", token_key)
    if match and match.group(1):
        candidates.append(f"{DEFAULT_API_URL_ENV}{match.group(1)}")
    candidates.append(DEFAULT_API_URL_ENV)
    return tuple(dict.fromkeys(candidates))


def _normalize_api_url(value: object | None) -> str | None:
    text = str(value or "").strip().rstrip("/")
    if not text:
        return None
    if not re.fullmatch(r"https?://[^/\s]+(?:/[^?\s#]*)?", text):
        raise ValueError(f"TuShare API URL must start with http:// or https://, got: {value}")
    return text


def resolve_tushare_api_url(
    api_url: str | None = None,
    *,
    token_env: str = "TUSHARE_TOKEN",
) -> str | None:
    _load_tushare_env_files()
    explicit = _normalize_api_url(api_url)
    if explicit:
        return explicit
    for env_key in _api_url_env_candidates(token_env):
        resolved = _normalize_api_url(os.environ.get(env_key))
        if resolved:
            return resolved
    return None


def resolve_tushare_api_urls(
    api_url: str | None = None,
    *,
    token_env: str = "TUSHARE_TOKEN",
) -> tuple[str | None, ...]:
    """Resolve ordered TuShare endpoints, with proxy failover for TOKEN_2."""

    explicit = _normalize_api_url(api_url)
    if explicit:
        return (explicit,)
    _load_tushare_env_files()
    suffix = str(token_env or "").removeprefix("TUSHARE_TOKEN").strip("_")
    list_key = f"TUSHARE_API_URLS_{suffix}" if suffix else "TUSHARE_API_URLS"
    configured = [
        normalized
        for raw in os.environ.get(list_key, "").split(",")
        if (normalized := _normalize_api_url(raw))
    ]
    first = resolve_tushare_api_url(token_env=token_env)
    if token_env != "TUSHARE_TOKEN_2":
        return tuple(dict.fromkeys([*configured, first]))
    candidates = [*configured]
    if first:
        candidates.append(first)
    return tuple(dict.fromkeys(candidates))

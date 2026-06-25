from __future__ import annotations

import os
import stat
import sys
from pathlib import Path
from typing import Literal

import tomli_w
from platformdirs import user_config_dir
from pydantic import BaseModel, ConfigDict

if sys.version_info >= (3, 11):
    import tomllib
else:
    import tomli as tomllib


class AuthConfig(BaseModel):
    model_config = ConfigDict(frozen=True)

    jira: Literal["pat", "basic"] = "pat"
    confluence: Literal["pat", "basic"] = "pat"
    bitbucket: Literal["pat", "basic"] = "pat"
    bamboo: Literal["pat", "basic"] = "basic"


class Profile(BaseModel):
    model_config = ConfigDict(frozen=False)

    jira_url: str | None = None
    confluence_url: str | None = None
    bitbucket_url: str | None = None
    bamboo_url: str | None = None
    auth: AuthConfig = AuthConfig()
    storage: Literal["env", "keyring", "plaintext", "command"] = "env"
    credential_command: str | None = None
    # Per-product command overrides for storage="command". When set, each takes
    # priority over the shared `credential_command` for that product — lets one
    # profile pull jira/confluence/bitbucket tokens from different vault entries.
    jira_command: str | None = None
    confluence_command: str | None = None
    bitbucket_command: str | None = None
    ca_bundle: str | None = None
    extra_headers: dict[str, str] = {}
    env_file: str | None = None


class Config(BaseModel):
    model_config = ConfigDict(frozen=False)

    default_profile: str = "default"
    profiles: dict[str, Profile] = {}


def config_path() -> Path:
    """Return the default config file path."""
    return Path(user_config_dir("atlassian-skills")) / "config.toml"


def load_config(path: Path | None = None) -> Config:
    """Load config from TOML file. Returns empty Config if file does not exist."""
    target = path if path is not None else config_path()
    if not target.exists():
        return Config()
    with target.open("rb") as f:
        data = tomllib.load(f)
    return Config.model_validate(data)


def save_config(config: Config, path: Path | None = None) -> None:
    """Serialize config to TOML and write to disk with restricted permissions."""
    target = path if path is not None else config_path()
    target.parent.mkdir(parents=True, exist_ok=True)
    data = config.model_dump(exclude_none=True)
    # Write with restricted permissions (owner read/write only)
    fd = os.open(str(target), os.O_WRONLY | os.O_CREAT | os.O_TRUNC, 0o600)
    with os.fdopen(fd, "wb") as f:
        tomli_w.dump(data, f)
    # Ensure permissions even if file existed with broader perms
    os.chmod(str(target), stat.S_IRUSR | stat.S_IWUSR)


def get_profile(config: Config, name: str | None = None) -> Profile:
    """Return the named profile, or the default profile if name is None."""
    profile_name = name if name is not None else config.default_profile
    return config.profiles.get(profile_name, Profile())


# Legacy env var names tried in order after ATLS_{PROFILE}_{PRODUCT}_TOKEN.
# Each product lists its accepted names from most to least specific.
_LEGACY_TOKEN_VARS: dict[str, list[str]] = {
    "jira": ["JIRA_PERSONAL_TOKEN", "JIRA_API_TOKEN", "JIRA_TOKEN"],
    "confluence": ["CONFLUENCE_PERSONAL_TOKEN", "CONFLUENCE_API_TOKEN", "CONFLUENCE_TOKEN"],
    "bitbucket": ["BITBUCKET_TOKEN", "BITBUCKET_API_TOKEN", "BITBUCKET_PERSONAL_TOKEN"],
}

_LEGACY_USER_VARS: dict[str, str] = {
    "jira": "JIRA_USERNAME",
    "confluence": "CONFLUENCE_USERNAME",
    "bitbucket": "BITBUCKET_USERNAME",
}


def get_env_token(profile_name: str, product: str) -> str | None:
    """Read token from env. Priority: ATLS_{PROFILE}_{PRODUCT}_TOKEN > legacy vars."""
    key = f"ATLS_{profile_name.upper()}_{product.upper()}_TOKEN"
    val = os.environ.get(key)
    if val:
        return val
    for legacy_key in _LEGACY_TOKEN_VARS.get(product.lower(), []):
        val = os.environ.get(legacy_key)
        if val:
            return val
    return None


def get_env_user(profile_name: str, product: str) -> str | None:
    """Read user from env. Priority: ATLS_{PROFILE}_{PRODUCT}_USER > legacy vars."""
    key = f"ATLS_{profile_name.upper()}_{product.upper()}_USER"
    val = os.environ.get(key)
    if val:
        return val
    legacy_key = _LEGACY_USER_VARS.get(product.lower())
    if legacy_key:
        return os.environ.get(legacy_key)
    return None


def get_env_auth_method(profile_name: str, product: str) -> str | None:
    """Read ATLS_{PROFILE}_{PRODUCT}_AUTH from environment."""
    key = f"ATLS_{profile_name.upper()}_{product.upper()}_AUTH"
    return os.environ.get(key)


def apply_env_file(profile: Profile) -> None:
    """Load KEY=VALUE pairs from profile.env_file into os.environ as fallbacks.

    Called once per CLI invocation before credential/header resolution so that
    every tool that spawns atls as a subprocess gets the same values as an
    interactive shell — no shell functions or session-level exports required.

    Existing env vars take precedence (env_file is a fallback, not an override),
    so explicit exports and CI-injected secrets are never shadowed.
    Blank lines and lines starting with ``#`` are ignored.
    """
    if not profile.env_file:
        return
    path = Path(profile.env_file).expanduser()
    if not path.exists():
        return
    with path.open(encoding="utf-8", errors="replace") as fh:
        for line in fh:
            line = line.strip()
            if not line or line.startswith("#"):
                continue
            if "=" in line:
                key, _, value = line.partition("=")
                key = key.strip()
                if key and key not in os.environ:
                    os.environ[key] = value.strip()


def get_env_extra_headers(profile_name: str) -> dict[str, str]:
    """Read ATLS_{PROFILE}_EXTRA_HEADERS from environment.

    Format: comma-separated ``Key=Value`` pairs, e.g.
    ``X-Zero-Trust-Token=eyJ...,X-Custom=foo``.
    Splitting is done on the first ``=`` only so JWT values are preserved.
    """
    raw = os.environ.get(f"ATLS_{profile_name.upper()}_EXTRA_HEADERS", "")
    if not raw:
        return {}
    result: dict[str, str] = {}
    for pair in raw.split(","):
        pair = pair.strip()
        if "=" in pair:
            k, v = pair.split("=", 1)
            result[k.strip()] = v.strip()
    return result

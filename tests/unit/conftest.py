from __future__ import annotations

import pytest

from atlassian_skills.confluence.client import ConfluenceClient
from atlassian_skills.core.auth import Credential
from atlassian_skills.core.client import BaseClient
from atlassian_skills.jira.client import JiraClient


@pytest.fixture(autouse=True)
def _no_env_file(monkeypatch: pytest.MonkeyPatch) -> None:
    """Block apply_env_file from loading real credentials during unit tests.

    Unit tests load the real config.toml via load_config(), which may have
    env_file set to a real credentials file. Without this guard, apply_env_file
    mutates os.environ with live tokens, causing wizard tests to see pre-existing
    credentials and take different code paths than the test inputs expect.
    """
    import atlassian_skills.core.config as config_mod
    import atlassian_skills.cli.auth as auth_mod
    import atlassian_skills.cli.jira as jira_mod
    import atlassian_skills.cli.confluence as confluence_mod
    import atlassian_skills.cli.bitbucket as bitbucket_mod

    noop = lambda _profile: None  # noqa: E731
    monkeypatch.setattr(config_mod, "apply_env_file", noop)
    for mod in (auth_mod, jira_mod, confluence_mod, bitbucket_mod):
        if hasattr(mod, "apply_env_file"):
            monkeypatch.setattr(mod, "apply_env_file", noop)


@pytest.fixture
def bypass_tty_guard(monkeypatch: pytest.MonkeyPatch) -> None:
    """Make `_ensure_interactive_terminal` a no-op for wizard tests.

    `CliRunner` runs commands with a non-TTY stdin, which would otherwise hit the
    wizard's TTY guard and exit before any prompt is exercised. Tests that
    specifically validate the guard skip this fixture.
    """
    import atlassian_skills.cli.setup as setup_mod

    monkeypatch.setattr(setup_mod, "_ensure_interactive_terminal", lambda: None)


@pytest.fixture
def base_client(mock_credential: Credential, jira_base_url: str) -> BaseClient:
    return BaseClient(jira_base_url, mock_credential)


@pytest.fixture
def jira_client(mock_credential: Credential, jira_base_url: str) -> JiraClient:
    return JiraClient(jira_base_url, mock_credential)


@pytest.fixture
def confluence_client(mock_credential: Credential, confluence_base_url: str) -> ConfluenceClient:
    return ConfluenceClient(confluence_base_url, mock_credential)

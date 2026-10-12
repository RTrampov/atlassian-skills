"""Legacy token variable names are tried in order, and the wizard names the one that is set."""

from __future__ import annotations

import pytest

from atlassian_skills.cli.setup import _env_token_var
from atlassian_skills.core.config import get_env_token

JIRA_TOKEN_VARS = ("ATLS_DEFAULT_JIRA_TOKEN", "JIRA_PERSONAL_TOKEN", "JIRA_API_TOKEN", "JIRA_TOKEN")


@pytest.fixture
def no_jira_tokens(monkeypatch: pytest.MonkeyPatch) -> None:
    for name in JIRA_TOKEN_VARS:
        monkeypatch.delenv(name, raising=False)


def test_get_env_token_falls_back_through_legacy_names_in_order(
    no_jira_tokens: None, monkeypatch: pytest.MonkeyPatch
) -> None:
    monkeypatch.setenv("JIRA_TOKEN", "last-resort")
    assert get_env_token("default", "jira") == "last-resort"

    monkeypatch.setenv("JIRA_API_TOKEN", "middle")
    assert get_env_token("default", "jira") == "middle"

    monkeypatch.setenv("JIRA_PERSONAL_TOKEN", "first")
    assert get_env_token("default", "jira") == "first"


def test_env_token_var_names_the_legacy_variable_that_is_set(
    no_jira_tokens: None, monkeypatch: pytest.MonkeyPatch
) -> None:
    monkeypatch.setenv("JIRA_API_TOKEN", "value")
    assert _env_token_var("jira") == "JIRA_API_TOKEN"


def test_env_token_var_returns_none_when_no_name_is_set(no_jira_tokens: None) -> None:
    assert _env_token_var("jira") is None

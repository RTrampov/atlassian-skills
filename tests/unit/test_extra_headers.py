"""Per-profile extra HTTP headers: where they come from, precedence, and verbose masking.

Extra headers often carry proxy tokens, so the masking test matters as much as the
sending test: `--verbose` output lands in transcripts and bug reports.
"""

from __future__ import annotations

import os
from pathlib import Path

import httpx
import pytest
import respx

from atlassian_skills.core.auth import Credential
from atlassian_skills.core.client import BaseClient
from atlassian_skills.core.config import Profile, apply_env_file, get_env_extra_headers

BASE_URL = "https://jira.example.com"
PAT_CRED = Credential(method="pat", token="pat-super-secret-value")
PROXY_TOKEN = "zta-proxy-secret-value"


def test_get_env_extra_headers_splits_each_pair_on_first_equals(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setenv("ATLS_CORP_EXTRA_HEADERS", "X-Proxy=eyJhb.c2ln=,X-Team=platform")
    assert get_env_extra_headers("corp") == {"X-Proxy": "eyJhb.c2ln=", "X-Team": "platform"}


def test_get_env_extra_headers_unset_returns_empty(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.delenv("ATLS_GHOST_EXTRA_HEADERS", raising=False)
    assert get_env_extra_headers("ghost") == {}


def test_apply_env_file_fills_only_variables_that_are_unset(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    creds = tmp_path / "creds.env"
    creds.write_text("# comment\n\nATLS_FILETEST_EXTRA_HEADERS=X-A=1\nATLS_FILETEST_KEEP=from-file\n", encoding="utf-8")
    # setenv then delenv registers the name with monkeypatch, so the write apply_env_file makes is undone afterwards.
    monkeypatch.setenv("ATLS_FILETEST_EXTRA_HEADERS", "placeholder")
    monkeypatch.delenv("ATLS_FILETEST_EXTRA_HEADERS")
    monkeypatch.setenv("ATLS_FILETEST_KEEP", "from-shell")

    apply_env_file(Profile(env_file=str(creds)))

    assert os.environ["ATLS_FILETEST_EXTRA_HEADERS"] == "X-A=1"
    assert os.environ["ATLS_FILETEST_KEEP"] == "from-shell"


@respx.mock
def test_extra_headers_are_sent_with_each_request() -> None:
    route = respx.get(f"{BASE_URL}/rest/api/2/myself").mock(return_value=httpx.Response(200, json={}))
    client = BaseClient(BASE_URL, PAT_CRED, extra_headers={"X-Zero-Trust-Token": PROXY_TOKEN})

    client.get("/rest/api/2/myself")

    assert route.calls.last.request.headers["X-Zero-Trust-Token"] == PROXY_TOKEN


@respx.mock
def test_credential_header_wins_over_extra_header_of_the_same_name() -> None:
    route = respx.get(f"{BASE_URL}/rest/api/2/myself").mock(return_value=httpx.Response(200, json={}))
    client = BaseClient(BASE_URL, PAT_CRED, extra_headers={"Authorization": "Bearer spoofed"})

    client.get("/rest/api/2/myself")

    assert route.calls.last.request.headers["Authorization"] == "Bearer pat-super-secret-value"


@respx.mock
def test_verbose_request_log_masks_extra_header_values(capsys: pytest.CaptureFixture[str]) -> None:
    respx.get(f"{BASE_URL}/rest/api/2/myself").mock(return_value=httpx.Response(200, json={}))
    client = BaseClient(BASE_URL, PAT_CRED, verbose=2, extra_headers={"X-Zero-Trust-Token": PROXY_TOKEN})

    client.get("/rest/api/2/myself")

    err = capsys.readouterr().err
    assert "X-Zero-Trust-Token: <redacted" in err
    assert PROXY_TOKEN not in err

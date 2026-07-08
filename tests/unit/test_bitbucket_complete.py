from __future__ import annotations

import json
from pathlib import Path

import httpx
import pytest
import respx

from atlassian_skills.bitbucket.client import BitbucketClient
from atlassian_skills.bitbucket.models import BuildStatus, DiffStat, PullRequest, PullRequestComment, Task
from atlassian_skills.core.auth import Credential
from atlassian_skills.core.errors import ValidationError

FIXTURES = Path(__file__).parent.parent / "fixtures" / "bitbucket"
BASE_URL = "https://bitbucket.example.com"
API = "/rest/api/1.0"

cred = Credential(method="pat", token="test-token")
client = BitbucketClient(BASE_URL, cred)


@pytest.fixture(autouse=True)
def _reset_client_state() -> None:
    client._current_user_slug = None


def _load(name: str) -> dict:
    return json.loads((FIXTURES / name).read_text())


# ---------------------------------------------------------------------------
# update_comment (with auto-version)
# ---------------------------------------------------------------------------


@respx.mock
def test_update_comment_auto_version() -> None:
    current = {"id": 100, "text": "old text", "version": 2, "author": {"name": "a", "displayName": "A"}}
    updated = {"id": 100, "text": "new text", "version": 3, "author": {"name": "a", "displayName": "A"}}

    respx.get(f"{BASE_URL}{API}/projects/PROJ/repos/my-repo/pull-requests/1/comments/100").mock(
        return_value=httpx.Response(200, json=current)
    )
    route = respx.put(f"{BASE_URL}{API}/projects/PROJ/repos/my-repo/pull-requests/1/comments/100").mock(
        return_value=httpx.Response(200, json=updated)
    )

    result = client.update_comment("PROJ", "my-repo", 1, 100, text="new text")

    assert isinstance(result, PullRequestComment)
    assert result.text == "new text"
    sent = json.loads(route.calls[0].request.content)
    assert sent["version"] == 2
    assert sent["text"] == "new text"


# ---------------------------------------------------------------------------
# delete_comment (version as query param)
# ---------------------------------------------------------------------------


@respx.mock
def test_delete_comment_version_param() -> None:
    current = {"id": 100, "text": "text", "version": 2}
    respx.get(f"{BASE_URL}{API}/projects/PROJ/repos/my-repo/pull-requests/1/comments/100").mock(
        return_value=httpx.Response(200, json=current)
    )
    route = respx.delete(f"{BASE_URL}{API}/projects/PROJ/repos/my-repo/pull-requests/1/comments/100").mock(
        return_value=httpx.Response(204)
    )

    client.delete_comment("PROJ", "my-repo", 1, 100)

    params = dict(route.calls[0].request.url.params)
    assert params["version"] == "2"


# ---------------------------------------------------------------------------
# resolve_comment (thread resolution — threadResolved, NOT state)
# ---------------------------------------------------------------------------


@respx.mock
def test_resolve_comment_sends_thread_resolved_not_state() -> None:
    current = {"id": 100, "text": "Please fix this", "version": 1, "severity": "NORMAL", "state": "OPEN"}
    resolved = {
        "id": 100,
        "text": "Please fix this",
        "version": 2,
        "severity": "NORMAL",
        "state": "OPEN",
        "threadResolved": True,
    }

    respx.get(f"{BASE_URL}{API}/projects/PROJ/repos/my-repo/pull-requests/1/comments/100").mock(
        return_value=httpx.Response(200, json=current)
    )
    route = respx.put(f"{BASE_URL}{API}/projects/PROJ/repos/my-repo/pull-requests/1/comments/100").mock(
        return_value=httpx.Response(200, json=resolved)
    )

    result = client.resolve_comment("PROJ", "my-repo", 1, 100)

    assert result.thread_resolved is True
    assert result.state == "OPEN"  # untouched
    sent = json.loads(route.calls[0].request.content)
    assert sent["text"] == "Please fix this"
    assert sent["threadResolved"] is True
    assert "state" not in sent
    assert sent["version"] == 1


@respx.mock
def test_resolve_comment_raises_if_server_ignores_thread_resolved() -> None:
    current = {"id": 100, "text": "text", "version": 1}
    # Server echoes back without applying the change
    stale = {"id": 100, "text": "text", "version": 2, "threadResolved": False}

    respx.get(f"{BASE_URL}{API}/projects/PROJ/repos/my-repo/pull-requests/1/comments/100").mock(
        return_value=httpx.Response(200, json=current)
    )
    respx.put(f"{BASE_URL}{API}/projects/PROJ/repos/my-repo/pull-requests/1/comments/100").mock(
        return_value=httpx.Response(200, json=stale)
    )

    with pytest.raises(ValidationError):
        client.resolve_comment("PROJ", "my-repo", 1, 100)


# ---------------------------------------------------------------------------
# reopen_comment (thread resolution)
# ---------------------------------------------------------------------------


@respx.mock
def test_reopen_comment() -> None:
    current = {"id": 100, "text": "Fixed now", "version": 2, "threadResolved": True}
    reopened = {"id": 100, "text": "Fixed now", "version": 3, "threadResolved": False}

    respx.get(f"{BASE_URL}{API}/projects/PROJ/repos/my-repo/pull-requests/1/comments/100").mock(
        return_value=httpx.Response(200, json=current)
    )
    route = respx.put(f"{BASE_URL}{API}/projects/PROJ/repos/my-repo/pull-requests/1/comments/100").mock(
        return_value=httpx.Response(200, json=reopened)
    )

    result = client.reopen_comment("PROJ", "my-repo", 1, 100)

    assert result.thread_resolved is False
    sent = json.loads(route.calls[0].request.content)
    assert sent["threadResolved"] is False
    assert "state" not in sent


# ---------------------------------------------------------------------------
# resolve_task / reopen_task (task = severity=BLOCKER comment, state field)
# ---------------------------------------------------------------------------


@respx.mock
def test_resolve_task_sends_state_not_thread_resolved() -> None:
    current = {"id": 200, "text": "Fix the bug", "version": 0, "severity": "BLOCKER", "state": "OPEN"}
    resolved = {"id": 200, "text": "Fix the bug", "version": 1, "severity": "BLOCKER", "state": "RESOLVED"}

    respx.get(f"{BASE_URL}{API}/projects/PROJ/repos/my-repo/pull-requests/1/comments/200").mock(
        return_value=httpx.Response(200, json=current)
    )
    route = respx.put(f"{BASE_URL}{API}/projects/PROJ/repos/my-repo/pull-requests/1/comments/200").mock(
        return_value=httpx.Response(200, json=resolved)
    )

    result = client.resolve_task("PROJ", "my-repo", 1, 200)

    assert result.state == "RESOLVED"
    sent = json.loads(route.calls[0].request.content)
    assert sent["state"] == "RESOLVED"
    assert "threadResolved" not in sent
    assert sent["version"] == 0


@respx.mock
def test_resolve_task_rejects_normal_comment() -> None:
    current = {"id": 201, "text": "Just a note", "version": 0, "severity": "NORMAL", "state": "OPEN"}
    respx.get(f"{BASE_URL}{API}/projects/PROJ/repos/my-repo/pull-requests/1/comments/201").mock(
        return_value=httpx.Response(200, json=current)
    )
    put_route = respx.put(f"{BASE_URL}{API}/projects/PROJ/repos/my-repo/pull-requests/1/comments/201").mock(
        return_value=httpx.Response(200, json=current)
    )

    with pytest.raises(ValidationError, match=r"not a task \(severity=NORMAL\)"):
        client.resolve_task("PROJ", "my-repo", 1, 201)

    assert not put_route.called  # no write attempted


@respx.mock
def test_reopen_task_rejects_normal_comment() -> None:
    current = {"id": 202, "text": "Just a note", "version": 0, "severity": "NORMAL", "state": "OPEN"}
    respx.get(f"{BASE_URL}{API}/projects/PROJ/repos/my-repo/pull-requests/1/comments/202").mock(
        return_value=httpx.Response(200, json=current)
    )
    put_route = respx.put(f"{BASE_URL}{API}/projects/PROJ/repos/my-repo/pull-requests/1/comments/202").mock(
        return_value=httpx.Response(200, json=current)
    )

    with pytest.raises(ValidationError, match=r"not a task \(severity=NORMAL\)"):
        client.reopen_task("PROJ", "my-repo", 1, 202)

    assert not put_route.called


@respx.mock
def test_reopen_task_success() -> None:
    current = {"id": 203, "text": "Fix it", "version": 3, "severity": "BLOCKER", "state": "RESOLVED"}
    reopened = {"id": 203, "text": "Fix it", "version": 4, "severity": "BLOCKER", "state": "OPEN"}

    respx.get(f"{BASE_URL}{API}/projects/PROJ/repos/my-repo/pull-requests/1/comments/203").mock(
        return_value=httpx.Response(200, json=current)
    )
    respx.put(f"{BASE_URL}{API}/projects/PROJ/repos/my-repo/pull-requests/1/comments/203").mock(
        return_value=httpx.Response(200, json=reopened)
    )

    result = client.reopen_task("PROJ", "my-repo", 1, 203)

    assert result.state == "OPEN"


@respx.mock
def test_resolve_task_conflict_retries_once_with_fresh_version() -> None:
    """409 on the first PUT (stale auto-fetched version) triggers one refetch+retry."""
    first_get = {"id": 300, "text": "Fix it", "version": 0, "severity": "BLOCKER", "state": "OPEN"}
    second_get = {"id": 300, "text": "Fix it", "version": 1, "severity": "BLOCKER", "state": "OPEN"}
    resolved = {"id": 300, "text": "Fix it", "version": 2, "severity": "BLOCKER", "state": "RESOLVED"}

    get_route = respx.get(f"{BASE_URL}{API}/projects/PROJ/repos/my-repo/pull-requests/1/comments/300")
    get_route.side_effect = [
        httpx.Response(200, json=first_get),
        httpx.Response(200, json=second_get),
    ]
    put_route = respx.put(f"{BASE_URL}{API}/projects/PROJ/repos/my-repo/pull-requests/1/comments/300")
    put_route.side_effect = [
        httpx.Response(409, json={"message": "stale version"}),
        httpx.Response(200, json=resolved),
    ]

    result = client.resolve_task("PROJ", "my-repo", 1, 300)

    assert result.state == "RESOLVED"
    sent_versions = [json.loads(c.request.content)["version"] for c in put_route.calls]
    assert sent_versions == [0, 1]


@respx.mock
def test_resolve_comment_explicit_version_conflict_not_retried() -> None:
    """An explicit --version that's stale surfaces ConflictError instead of silently retrying."""
    from atlassian_skills.core.errors import ConflictError

    current = {"id": 400, "text": "text", "version": 5}
    respx.get(f"{BASE_URL}{API}/projects/PROJ/repos/my-repo/pull-requests/1/comments/400").mock(
        return_value=httpx.Response(200, json=current)
    )
    put_route = respx.put(f"{BASE_URL}{API}/projects/PROJ/repos/my-repo/pull-requests/1/comments/400").mock(
        return_value=httpx.Response(409, json={"message": "stale version"})
    )

    with pytest.raises(ConflictError):
        client.resolve_comment("PROJ", "my-repo", 1, 400, version=1)

    assert put_route.call_count == 1  # no retry when version was explicit


# ---------------------------------------------------------------------------
# get_pull_request_diffstat (/changes endpoint)
# ---------------------------------------------------------------------------


@respx.mock
def test_get_pull_request_diffstat_uses_changes() -> None:
    fixture = _load("diffstat-changes.json")
    route = respx.get(f"{BASE_URL}{API}/projects/PROJ/repos/my-repo/pull-requests/1/changes").mock(
        return_value=httpx.Response(200, json=fixture)
    )

    result = client.get_pull_request_diffstat("PROJ", "my-repo", 1)

    assert len(result) == 2
    assert isinstance(result[0], DiffStat)
    assert result[0].path.to_string == "src/main.py"
    assert result[0].type == "MODIFY"
    assert result[1].type == "ADD"
    assert route.called


# ---------------------------------------------------------------------------
# get_build_statuses (/rest/build-status/1.0/ — different API base)
# ---------------------------------------------------------------------------


@respx.mock
def test_get_build_statuses_uses_build_status_api() -> None:
    fixture = _load("build-status-list.json")
    route = respx.get(f"{BASE_URL}/rest/build-status/1.0/commits/abc123def456").mock(
        return_value=httpx.Response(200, json=fixture)
    )

    result = client.get_build_statuses("abc123def456")

    assert len(result) == 2
    assert isinstance(result[0], BuildStatus)
    assert result[0].state == "SUCCESSFUL"
    assert result[0].key == "ci-build"
    assert result[1].state == "FAILED"
    # Verify it uses the build-status API, not self.API
    assert "/rest/build-status/1.0/" in str(route.calls[0].request.url)


# ---------------------------------------------------------------------------
# list_pull_requests_for_reviewer (inbox with fallback)
# ---------------------------------------------------------------------------


@respx.mock
def test_list_pull_requests_for_reviewer() -> None:
    fixture = _load("pull-request-list.json")
    respx.get(f"{BASE_URL}{API}/inbox/pull-requests").mock(return_value=httpx.Response(200, json=fixture))

    result = client.list_pull_requests_for_reviewer()

    assert len(result) == 2
    assert isinstance(result[0], PullRequest)


@respx.mock
def test_list_pull_requests_for_reviewer_fallback() -> None:
    fixture = _load("pull-request-list.json")
    respx.get(f"{BASE_URL}{API}/inbox/pull-requests").mock(
        return_value=httpx.Response(404, json={"message": "Not found"})
    )
    respx.get(f"{BASE_URL}{API}/dashboard/pull-requests").mock(return_value=httpx.Response(200, json=fixture))

    result = client.list_pull_requests_for_reviewer()

    assert len(result) == 2


# ---------------------------------------------------------------------------
# Task CRUD (top-level /tasks endpoint)
# ---------------------------------------------------------------------------


@respx.mock
def test_list_tasks() -> None:
    fixture = _load("task-list.json")
    respx.get(f"{BASE_URL}{API}/projects/PROJ/repos/my-repo/pull-requests/1/tasks").mock(
        return_value=httpx.Response(200, json=fixture)
    )

    result = client.list_tasks("PROJ", "my-repo", 1)

    assert len(result) == 2
    assert isinstance(result[0], Task)
    assert result[0].id == 10
    assert result[0].state == "OPEN"
    assert result[1].state == "RESOLVED"


@respx.mock
def test_create_task_uses_top_level_endpoint() -> None:
    fixture = _load("task-create-expected.json")
    route = respx.post(f"{BASE_URL}{API}/tasks").mock(return_value=httpx.Response(201, json=fixture))

    result = client.create_task(text="Update documentation", comment_id=100)

    assert isinstance(result, Task)
    assert result.id == 12
    sent = json.loads(route.calls[0].request.content)
    assert sent["anchor"]["id"] == 100
    assert sent["anchor"]["type"] == "COMMENT"
    assert sent["text"] == "Update documentation"


@respx.mock
def test_update_task() -> None:
    updated = {"id": 10, "text": "Fix naming convention", "state": "RESOLVED"}
    respx.put(f"{BASE_URL}{API}/tasks/10").mock(return_value=httpx.Response(200, json=updated))

    result = client.update_task(10, state="RESOLVED")

    assert result.state == "RESOLVED"


@respx.mock
def test_delete_task() -> None:
    respx.delete(f"{BASE_URL}{API}/tasks/10").mock(return_value=httpx.Response(204))

    client.delete_task(10)
    # No exception = success

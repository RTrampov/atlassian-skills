from __future__ import annotations

import json
from pathlib import Path

import httpx
import pytest
import respx

from atlassian_skills.bitbucket.client import BitbucketClient
from atlassian_skills.bitbucket.models import (
    Branch,
    Commit,
    PullRequest,
    PullRequestActivity,
    PullRequestComment,
)
from atlassian_skills.core.auth import Credential

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
# list_pull_requests
# ---------------------------------------------------------------------------


@respx.mock
def test_list_pull_requests() -> None:
    fixture = _load("pull-request-list.json")
    respx.get(f"{BASE_URL}{API}/projects/PROJ/repos/my-repo/pull-requests").mock(
        return_value=httpx.Response(200, json=fixture)
    )

    result = client.list_pull_requests("PROJ", "my-repo")

    assert len(result) == 2
    assert isinstance(result[0], PullRequest)
    assert result[0].id == 1
    assert result[0].title == "Add feature X"
    assert result[0].state == "OPEN"
    assert result[0].author is not None
    assert result[0].author.user.display_name == "John Smith"
    assert len(result[0].reviewers) == 2
    assert result[0].reviewers[0].status == "APPROVED"
    assert result[0].reviewers[1].status == "NEEDS_WORK"
    assert result[1].state == "MERGED"


@respx.mock
def test_list_pull_requests_with_state_filter() -> None:
    fixture = _load("pull-request-list.json")
    route = respx.get(f"{BASE_URL}{API}/projects/PROJ/repos/my-repo/pull-requests").mock(
        return_value=httpx.Response(200, json=fixture)
    )

    client.list_pull_requests("PROJ", "my-repo", state="open")

    params = dict(route.calls[0].request.url.params)
    assert params["state"] == "OPEN"


# ---------------------------------------------------------------------------
# get_pull_request
# ---------------------------------------------------------------------------


@respx.mock
def test_get_pull_request() -> None:
    fixture = _load("pull-request-get.json")
    respx.get(f"{BASE_URL}{API}/projects/PROJ/repos/my-repo/pull-requests/1").mock(
        return_value=httpx.Response(200, json=fixture)
    )

    pr = client.get_pull_request("PROJ", "my-repo", 1)

    assert isinstance(pr, PullRequest)
    assert pr.id == 1
    assert pr.version == 3
    assert pr.from_ref is not None
    assert pr.from_ref.display_id == "feature/x"
    assert pr.to_ref is not None
    assert pr.to_ref.display_id == "main"


# ---------------------------------------------------------------------------
# get_pull_request_diff
# ---------------------------------------------------------------------------


@respx.mock
def test_get_pull_request_diff() -> None:
    diff_text = "diff --git a/src/main.py b/src/main.py\n--- a/src/main.py\n+++ b/src/main.py\n@@ -1,3 +1,4 @@\n+import os\n import sys\n"
    respx.get(f"{BASE_URL}{API}/projects/PROJ/repos/my-repo/pull-requests/1/diff").mock(
        return_value=httpx.Response(200, text=diff_text, headers={"content-type": "text/plain"})
    )

    result = client.get_pull_request_diff("PROJ", "my-repo", 1)

    assert "diff --git" in result
    assert "+import os" in result


@respx.mock
def test_get_pull_request_diff_with_path() -> None:
    diff_text = "--- a/src/main.py\n+++ b/src/main.py\n"
    respx.get(f"{BASE_URL}{API}/projects/PROJ/repos/my-repo/pull-requests/1/diff/src/main.py").mock(
        return_value=httpx.Response(200, text=diff_text, headers={"content-type": "text/plain"})
    )

    result = client.get_pull_request_diff("PROJ", "my-repo", 1, path="src/main.py")

    assert "src/main.py" in result


# ---------------------------------------------------------------------------
# list_pull_request_comments
# ---------------------------------------------------------------------------


@respx.mock
def test_list_pull_request_comments() -> None:
    # Activities with COMMENTED actions — comments extracted from these
    activities_with_comments = {
        "size": 3,
        "limit": 25,
        "isLastPage": True,
        "values": [
            {
                "id": 10,
                "action": "COMMENTED",
                "createdDate": 1713200000000,
                "comment": {
                    "id": 100,
                    "text": "Looks good overall, but please fix the naming",
                    "author": {"name": "alice", "displayName": "Alice Lee"},
                    "severity": "NORMAL",
                    "state": "OPEN",
                    "version": 0,
                    "comments": [
                        {
                            "id": 101,
                            "text": "Fixed, thanks!",
                            "author": {"name": "jsmith", "displayName": "John Smith"},
                            "severity": "NORMAL",
                            "state": "OPEN",
                            "version": 0,
                            "comments": [],
                        }
                    ],
                },
            },
            {
                "id": 11,
                "action": "COMMENTED",
                "createdDate": 1713220000000,
                "comment": {
                    "id": 102,
                    "text": "This line needs refactoring",
                    "author": {"name": "bob", "displayName": "Bob Kim"},
                    "severity": "BLOCKER",
                    "state": "RESOLVED",
                    "version": 1,
                    "anchor": {"path": "src/main.py", "line": 42, "lineType": "ADDED", "fileType": "TO"},
                    "comments": [],
                },
            },
            {"id": 12, "action": "OPENED", "createdDate": 1713190000000},
        ],
        "start": 0,
    }
    respx.get(f"{BASE_URL}{API}/projects/PROJ/repos/my-repo/pull-requests/1/activities").mock(
        return_value=httpx.Response(200, json=activities_with_comments)
    )
    # Live per-comment enrichment (state/threadResolved as of now, not at post time)
    respx.get(f"{BASE_URL}{API}/projects/PROJ/repos/my-repo/pull-requests/1/comments/100").mock(
        return_value=httpx.Response(
            200,
            json={
                "id": 100,
                "text": "Looks good overall, but please fix the naming",
                "state": "OPEN",
                "threadResolved": False,
                "version": 0,
            },
        )
    )
    respx.get(f"{BASE_URL}{API}/projects/PROJ/repos/my-repo/pull-requests/1/comments/102").mock(
        return_value=httpx.Response(
            200,
            json={
                "id": 102,
                "text": "This line needs refactoring",
                "state": "RESOLVED",
                "threadResolved": True,
                "version": 1,
            },
        )
    )

    result = client.list_pull_request_comments("PROJ", "my-repo", 1)

    assert len(result) == 2  # Only COMMENTED activities, not OPENED
    assert isinstance(result[0], PullRequestComment)
    assert result[0].id == 100
    assert result[0].text == "Looks good overall, but please fix the naming"
    assert result[0].thread_resolved is False
    # Threaded reply (untouched by enrichment)
    assert len(result[0].comments) == 1
    assert result[0].comments[0].id == 101
    # Inline comment with anchor
    assert result[1].anchor is not None
    assert result[1].anchor.path == "src/main.py"
    assert result[1].anchor.line == 42
    assert result[1].state == "RESOLVED"
    assert result[1].thread_resolved is True


@respx.mock
def test_list_pull_request_comments_activity_level_anchor() -> None:
    # Real Bitbucket Server places inline-comment diff anchors at the ACTIVITY
    # level (`commentAnchor`, sibling of `comment`); `comment.anchor` is null.
    activities = {
        "size": 1,
        "limit": 25,
        "isLastPage": True,
        "values": [
            {
                "id": 91513059,
                "action": "COMMENTED",
                "createdDate": 1782397270038,
                "comment": {
                    "id": 3836879,
                    "text": "`go mod tidy`?",
                    "author": {"name": "nik", "displayName": "Nikolay Bekirov"},
                    "severity": "NORMAL",
                    "state": "OPEN",
                    "version": 0,
                    "anchor": None,
                    "comments": [],
                },
                "commentAnchor": {
                    "fromHash": "b329277c",
                    "toHash": "2a87a5d9",
                    "line": 43,
                    "lineType": "CONTEXT",
                    "fileType": "FROM",
                    "path": "go.sum",
                    "diffType": "EFFECTIVE",
                    "orphaned": False,
                },
            },
        ],
        "start": 0,
    }
    respx.get(f"{BASE_URL}{API}/projects/PROJ/repos/my-repo/pull-requests/1/activities").mock(
        return_value=httpx.Response(200, json=activities)
    )
    respx.get(f"{BASE_URL}{API}/projects/PROJ/repos/my-repo/pull-requests/1/comments/3836879").mock(
        return_value=httpx.Response(
            200, json={"id": 3836879, "text": "`go mod tidy`?", "state": "OPEN", "threadResolved": False, "version": 0}
        )
    )

    result = client.list_pull_request_comments("PROJ", "my-repo", 1)

    assert len(result) == 1
    anchor = result[0].anchor
    assert anchor is not None
    assert anchor.path == "go.sum"
    assert anchor.line == 43
    assert anchor.line_type == "CONTEXT"
    assert anchor.file_type == "FROM"
    assert anchor.from_hash == "b329277c"
    assert anchor.to_hash == "2a87a5d9"
    assert anchor.diff_type == "EFFECTIVE"
    assert anchor.orphaned is False


@respx.mock
def test_list_pull_request_activities_exposes_comment_anchor() -> None:
    activities = {
        "size": 1,
        "limit": 25,
        "isLastPage": True,
        "values": [
            {
                "id": 91513059,
                "action": "COMMENTED",
                "createdDate": 1782397270038,
                "comment": {
                    "id": 3836879,
                    "text": "inline note",
                    "author": {"name": "nik", "displayName": "Nikolay Bekirov"},
                    "state": "OPEN",
                    "version": 0,
                    "anchor": None,
                    "comments": [],
                },
                "commentAnchor": {"line": 43, "path": "go.sum", "lineType": "CONTEXT"},
            },
        ],
        "start": 0,
    }
    respx.get(f"{BASE_URL}{API}/projects/PROJ/repos/my-repo/pull-requests/1/activities").mock(
        return_value=httpx.Response(200, json=activities)
    )

    result = client.list_pull_request_activities("PROJ", "my-repo", 1)

    assert len(result) == 1
    # Exposed both at activity level and merged onto the comment.
    assert result[0].comment_anchor is not None
    assert result[0].comment_anchor.line == 43
    assert result[0].comment is not None
    assert result[0].comment.anchor is not None
    assert result[0].comment.anchor.path == "go.sum"


# ---------------------------------------------------------------------------
# list_pull_request_comments — thread-resolved enrichment (regression)
# ---------------------------------------------------------------------------


@respx.mock
def test_list_pull_request_comments_reports_thread_resolved_despite_stale_activity_state() -> None:
    """Bitbucket never rewrites the activity feed after a comment is resolved,
    so the activity-derived `state` is always "OPEN". The live per-comment
    fetch must surface the real, current `threadResolved` value instead.
    """
    activities = {
        "size": 1,
        "limit": 25,
        "isLastPage": True,
        "values": [
            {
                "id": 20,
                "action": "COMMENTED",
                "createdDate": 1713200000000,
                "comment": {
                    "id": 200,
                    "text": "Please double check this",
                    "author": {"name": "alice", "displayName": "Alice Lee"},
                    "severity": "NORMAL",
                    "state": "OPEN",  # stale — thread was resolved after this snapshot
                    "version": 0,
                    "comments": [],
                },
            },
        ],
        "start": 0,
    }
    respx.get(f"{BASE_URL}{API}/projects/PROJ/repos/my-repo/pull-requests/1/activities").mock(
        return_value=httpx.Response(200, json=activities)
    )
    respx.get(f"{BASE_URL}{API}/projects/PROJ/repos/my-repo/pull-requests/1/comments/200").mock(
        return_value=httpx.Response(
            200,
            json={"id": 200, "text": "Please double check this", "state": "OPEN", "threadResolved": True, "version": 0},
        )
    )

    result = client.list_pull_request_comments("PROJ", "my-repo", 1)

    assert len(result) == 1
    assert result[0].state == "OPEN"  # per-comment flag, largely vestigial
    assert result[0].thread_resolved is True  # what the Bitbucket UI actually shows


@respx.mock
def test_list_pull_request_comments_skips_enrichment_when_disabled() -> None:
    activities = {
        "size": 1,
        "limit": 25,
        "isLastPage": True,
        "values": [
            {
                "id": 21,
                "action": "COMMENTED",
                "createdDate": 1713200000000,
                "comment": {
                    "id": 201,
                    "text": "Some comment",
                    "author": {"name": "alice", "displayName": "Alice Lee"},
                    "severity": "NORMAL",
                    "state": "OPEN",
                    "version": 0,
                    "comments": [],
                },
            },
        ],
        "start": 0,
    }
    route = respx.get(f"{BASE_URL}{API}/projects/PROJ/repos/my-repo/pull-requests/1/activities").mock(
        return_value=httpx.Response(200, json=activities)
    )
    # No mock registered for GET .../comments/201 — if the client tried to call
    # it, respx would raise and fail the test.

    result = client.list_pull_request_comments("PROJ", "my-repo", 1, include_thread_resolved=False)

    assert route.called
    assert len(result) == 1
    assert result[0].thread_resolved is None


# ---------------------------------------------------------------------------
# list_pull_request_commits
# ---------------------------------------------------------------------------


@respx.mock
def test_list_pull_request_commits() -> None:
    fixture = _load("pull-request-commits.json")
    respx.get(f"{BASE_URL}{API}/projects/PROJ/repos/my-repo/pull-requests/1/commits").mock(
        return_value=httpx.Response(200, json=fixture)
    )

    result = client.list_pull_request_commits("PROJ", "my-repo", 1)

    assert len(result) == 2
    assert isinstance(result[0], Commit)
    assert result[0].display_id == "abc123d"
    assert result[0].message is not None
    assert result[0].message.startswith("Add feature X")


# ---------------------------------------------------------------------------
# list_pull_request_activities
# ---------------------------------------------------------------------------


@respx.mock
def test_list_pull_request_activities() -> None:
    fixture = _load("pull-request-activities.json")
    respx.get(f"{BASE_URL}{API}/projects/PROJ/repos/my-repo/pull-requests/1/activities").mock(
        return_value=httpx.Response(200, json=fixture)
    )

    result = client.list_pull_request_activities("PROJ", "my-repo", 1)

    assert len(result) == 3
    assert isinstance(result[0], PullRequestActivity)
    assert result[0].action == "OPENED"
    assert result[1].action == "APPROVED"
    assert result[2].action == "COMMENTED"
    assert result[2].comment is not None
    assert result[2].comment.id == 102


# ---------------------------------------------------------------------------
# list_branches
# ---------------------------------------------------------------------------


@respx.mock
def test_list_branches() -> None:
    fixture = _load("branch-list.json")
    respx.get(f"{BASE_URL}{API}/projects/PROJ/repos/my-repo/branches").mock(
        return_value=httpx.Response(200, json=fixture)
    )

    result = client.list_branches("PROJ", "my-repo")

    assert len(result) == 3
    assert isinstance(result[0], Branch)
    assert result[0].display_id == "main"
    assert result[0].is_default is True
    assert result[1].display_id == "feature/x"
    assert result[1].is_default is False


@respx.mock
def test_list_branches_with_filter() -> None:
    fixture = _load("branch-list.json")
    route = respx.get(f"{BASE_URL}{API}/projects/PROJ/repos/my-repo/branches").mock(
        return_value=httpx.Response(200, json=fixture)
    )

    client.list_branches("PROJ", "my-repo", filter_text="feature")

    params = dict(route.calls[0].request.url.params)
    assert params["filterText"] == "feature"


# ---------------------------------------------------------------------------
# get_file_content
# ---------------------------------------------------------------------------


@respx.mock
def test_get_file_content() -> None:
    file_text = "import sys\n\ndef main():\n    print('hello')\n"
    respx.get(f"{BASE_URL}{API}/projects/PROJ/repos/my-repo/raw/src/main.py").mock(
        return_value=httpx.Response(200, text=file_text, headers={"content-type": "text/plain"})
    )

    result = client.get_file_content("PROJ", "my-repo", "src/main.py")

    assert "import sys" in result
    assert "def main():" in result


@respx.mock
def test_get_file_content_with_ref() -> None:
    file_text = "# old version"
    route = respx.get(f"{BASE_URL}{API}/projects/PROJ/repos/my-repo/raw/README.md").mock(
        return_value=httpx.Response(200, text=file_text, headers={"content-type": "text/plain"})
    )

    client.get_file_content("PROJ", "my-repo", "README.md", at="develop")

    params = dict(route.calls[0].request.url.params)
    assert params["at"] == "develop"

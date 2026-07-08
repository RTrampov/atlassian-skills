from __future__ import annotations

from typing import Any

from atlassian_skills.bitbucket.models import (
    Branch,
    BuildStatus,
    Commit,
    DiffStat,
    Project,
    PullRequest,
    PullRequestActivity,
    PullRequestComment,
    PullRequestParticipant,
    Repository,
    Task,
)
from atlassian_skills.core.auth import Credential
from atlassian_skills.core.client import BaseClient
from atlassian_skills.core.errors import ConflictError, NotFoundError, ValidationError


def _merge_comment_anchor(activity: dict[str, Any]) -> dict[str, Any]:
    """Surface a COMMENTED activity's inline anchor on its comment.

    Bitbucket Server returns inline-comment diff anchors at the *activity* level
    (``commentAnchor``, a sibling of ``comment``) rather than inside
    ``comment.anchor``, which arrives ``null``. Copy it onto the comment so the
    line number / path / lineType are available on the comment itself.

    Returns the activity dict (mutated copy) when an anchor is merged, otherwise
    the original object unchanged.
    """
    comment = activity.get("comment")
    anchor = activity.get("commentAnchor")
    if not isinstance(comment, dict) or not isinstance(anchor, dict):
        return activity
    if comment.get("anchor"):
        return activity
    merged = dict(activity)
    merged["comment"] = {**comment, "anchor": anchor}
    return merged


class BitbucketClient(BaseClient):
    """Bitbucket Server/DC REST API client.

    API base: /rest/api/1.0
    Pagination: start/limit/nextPageStart/isLastPage (offset-based).
    """

    API = "/rest/api/1.0"

    def __init__(
        self,
        base_url: str,
        credential: Credential,
        timeout: float = 30.0,
        verify: str | bool = True,
        extra_headers: dict[str, str] | None = None,
    ) -> None:
        super().__init__(base_url, credential, timeout, verify=verify, extra_headers=extra_headers)
        self._current_user_slug: str | None = None

    # ------------------------------------------------------------------
    # Pagination
    # ------------------------------------------------------------------

    def _get_paged(
        self,
        path: str,
        *,
        params: dict[str, Any] | None = None,
        limit: int = 25,
    ) -> list[dict[str, Any]]:
        """Fetch all pages from a Bitbucket Server paginated endpoint."""
        if params is None:
            params = {}
        params["limit"] = limit

        items: list[dict[str, Any]] = []
        while True:
            resp = self.get(f"{self.API}{path}", params=params)
            data = resp.json()
            items.extend(data.get("values", []))
            if data.get("isLastPage", True):
                break
            next_start = data.get("nextPageStart")
            if next_start is None:
                break
            params["start"] = next_start
        return items

    # ------------------------------------------------------------------
    # Current user
    # ------------------------------------------------------------------

    def _get_current_user_slug(self) -> str:
        """Get the authenticated user's slug (cached after first call).

        Tries X-AUSERNAME header from any authenticated request first,
        then falls back to /plugins/servlet/applinks/whoami.
        """
        if self._current_user_slug is not None:
            return self._current_user_slug
        # Primary: X-AUSERNAME header from a lightweight API call
        resp = self.get(f"{self.API}/users", params={"limit": 1})
        slug = resp.headers.get("X-AUSERNAME", "").strip()
        if not slug:
            # Fallback: whoami servlet
            resp2 = self.get("/plugins/servlet/applinks/whoami")
            slug = resp2.text.strip()
        if not slug:
            raise ValidationError("Could not determine current user. Check authentication.")
        self._current_user_slug = str(slug)
        return self._current_user_slug

    # ------------------------------------------------------------------
    # Projects
    # ------------------------------------------------------------------

    def list_projects(self, *, name: str | None = None, limit: int = 25) -> list[Project]:
        """GET /rest/api/1.0/projects"""
        params: dict[str, Any] = {}
        if name:
            params["name"] = name
        items = self._get_paged("/projects", params=params, limit=limit)
        return [Project.model_validate(i) for i in items]

    def get_project(self, key: str) -> Project:
        """GET /rest/api/1.0/projects/{projectKey}"""
        data = self.get(f"{self.API}/projects/{key}").json()
        return Project.model_validate(data)

    # ------------------------------------------------------------------
    # Repositories
    # ------------------------------------------------------------------

    def list_repos(self, project_key: str, *, limit: int = 25) -> list[Repository]:
        """GET /rest/api/1.0/projects/{projectKey}/repos"""
        items = self._get_paged(f"/projects/{project_key}/repos", limit=limit)
        return [Repository.model_validate(i) for i in items]

    def get_repo(self, project_key: str, slug: str) -> Repository:
        """GET /rest/api/1.0/projects/{projectKey}/repos/{repoSlug}"""
        data = self.get(f"{self.API}/projects/{project_key}/repos/{slug}").json()
        return Repository.model_validate(data)

    # ------------------------------------------------------------------
    # Pull Requests — Read (Phase 1)
    # ------------------------------------------------------------------

    def _pr_path(self, project: str, repo: str) -> str:
        return f"/projects/{project}/repos/{repo}/pull-requests"

    def list_pull_requests(
        self,
        project: str,
        repo: str,
        *,
        state: str | None = None,
        limit: int = 25,
    ) -> list[PullRequest]:
        """GET .../pull-requests"""
        params: dict[str, Any] = {}
        if state:
            params["state"] = state.upper()
        items = self._get_paged(self._pr_path(project, repo), params=params, limit=limit)
        return [PullRequest.model_validate(i) for i in items]

    def get_pull_request(self, project: str, repo: str, pr_id: int) -> PullRequest:
        """GET .../pull-requests/{id}"""
        data = self.get(f"{self.API}{self._pr_path(project, repo)}/{pr_id}").json()
        return PullRequest.model_validate(data)

    def get_pull_request_diff(
        self,
        project: str,
        repo: str,
        pr_id: int,
        *,
        path: str | None = None,
        context_lines: int | None = None,
    ) -> str:
        """GET .../pull-requests/{id}/diff — returns raw unified diff text."""
        url = f"{self.API}{self._pr_path(project, repo)}/{pr_id}/diff"
        if path:
            url = f"{url}/{path}"
        params: dict[str, Any] = {}
        if context_lines is not None:
            params["contextLines"] = context_lines
        resp = self.request("GET", url, params=params, headers={"Accept": "text/plain"})
        return resp.text

    def list_pull_request_comments(
        self,
        project: str,
        repo: str,
        pr_id: int,
        *,
        limit: int = 25,
        include_thread_resolved: bool = True,
    ) -> list[PullRequestComment]:
        """Extract comments from PR activities.

        Bitbucket Server's /comments endpoint requires a path parameter.
        Instead, we use /activities and filter for COMMENTED actions.

        The activity feed is a point-in-time snapshot taken when the comment
        was posted: its `state` field is essentially always "OPEN" and never
        reflects a later resolve/reopen. To report the *current* resolved
        status (what the Bitbucket UI shows as the "Resolved" badge), each
        comment is enriched with a live GET .../comments/{id} call, which
        exposes `state` and `threadResolved` as of now. Set
        `include_thread_resolved=False` to skip these extra requests (one per
        comment) if only the historical activity snapshot is needed.
        """
        items = self._get_paged(
            f"{self._pr_path(project, repo)}/{pr_id}/activities",
            limit=limit,
        )
        comments: list[PullRequestComment] = []
        for item in items:
            if item.get("action") == "COMMENTED" and item.get("comment"):
                merged = _merge_comment_anchor(item)
                comment_dict = merged["comment"]
                if include_thread_resolved:
                    comment_dict = self._enrich_comment_live_state(project, repo, pr_id, comment_dict)
                comments.append(PullRequestComment.model_validate(comment_dict))
        return comments

    def _enrich_comment_live_state(
        self, project: str, repo: str, pr_id: int, comment_dict: dict[str, Any]
    ) -> dict[str, Any]:
        """Overlay live `state`/`threadResolved` onto an activity-derived comment dict.

        Only a small set of keys are copied from the live response: it lacks
        `anchor` (Bitbucket Server only returns diff anchors via the
        activities feed) and its own `comments` (replies) would otherwise
        clobber the replies already parsed from the activity payload.
        """
        comment_id = comment_dict.get("id")
        if comment_id is None:
            return comment_dict
        try:
            live = self._get_comment(project, repo, pr_id, comment_id)
        except NotFoundError:
            return comment_dict
        enriched = dict(comment_dict)
        for key in ("state", "threadResolved", "version", "updatedDate", "text"):
            if key in live:
                enriched[key] = live[key]
        return enriched

    def list_pull_request_commits(self, project: str, repo: str, pr_id: int, *, limit: int = 25) -> list[Commit]:
        """GET .../pull-requests/{id}/commits"""
        items = self._get_paged(
            f"{self._pr_path(project, repo)}/{pr_id}/commits",
            limit=limit,
        )
        return [Commit.model_validate(i) for i in items]

    def list_pull_request_activities(
        self, project: str, repo: str, pr_id: int, *, limit: int = 25
    ) -> list[PullRequestActivity]:
        """GET .../pull-requests/{id}/activities"""
        items = self._get_paged(
            f"{self._pr_path(project, repo)}/{pr_id}/activities",
            limit=limit,
        )
        return [PullRequestActivity.model_validate(_merge_comment_anchor(i)) for i in items]

    # ------------------------------------------------------------------
    # Branches (Phase 1)
    # ------------------------------------------------------------------

    def list_branches(
        self,
        project: str,
        repo: str,
        *,
        filter_text: str | None = None,
        limit: int = 25,
    ) -> list[Branch]:
        """GET .../branches"""
        params: dict[str, Any] = {}
        if filter_text:
            params["filterText"] = filter_text
        items = self._get_paged(f"/projects/{project}/repos/{repo}/branches", params=params, limit=limit)
        return [Branch.model_validate(i) for i in items]

    # ------------------------------------------------------------------
    # File content (Phase 1)
    # ------------------------------------------------------------------

    def get_file_content(self, project: str, repo: str, path: str, *, at: str | None = None) -> str:
        """GET .../raw/{path} — returns raw file content (byte-preserving)."""
        params: dict[str, Any] = {}
        if at:
            params["at"] = at
        resp = self.get(f"{self.API}/projects/{project}/repos/{repo}/raw/{path}", params=params)
        content_type = resp.headers.get("content-type", "")
        binary_types = ("application/octet-stream", "image/", "video/", "audio/", "application/zip", "application/gzip")
        if any(bt in content_type for bt in binary_types):
            raise ValidationError("Binary file cannot be displayed as text")
        return resp.text

    # ------------------------------------------------------------------
    # Pull Requests — Write (Phase 2)
    # ------------------------------------------------------------------

    def create_pull_request(
        self,
        project: str,
        repo: str,
        *,
        title: str,
        from_ref: str,
        to_ref: str,
        description: str | None = None,
        reviewers: list[str] | None = None,
    ) -> PullRequest:
        """POST .../pull-requests"""
        payload: dict[str, Any] = {
            "title": title,
            "fromRef": {"id": f"refs/heads/{from_ref}"},
            "toRef": {"id": f"refs/heads/{to_ref}"},
        }
        if description:
            payload["description"] = description
        if reviewers:
            payload["reviewers"] = [{"user": {"name": r}} for r in reviewers]
        data = self.post(f"{self.API}{self._pr_path(project, repo)}", json=payload).json()
        return PullRequest.model_validate(data)

    def update_pull_request(
        self,
        project: str,
        repo: str,
        pr_id: int,
        *,
        title: str | None = None,
        description: str | None = None,
        reviewers: list[str] | None = None,
        version: int | None = None,
    ) -> PullRequest:
        """PUT .../pull-requests/{id}"""
        if version is None or title is None:
            pr = self.get_pull_request(project, repo, pr_id)
            if version is None:
                version = pr.version
            if title is None:
                title = pr.title
        payload: dict[str, Any] = {
            "version": version,
            "title": title,
        }
        if description is not None:
            payload["description"] = description
        if reviewers is not None:
            payload["reviewers"] = [{"user": {"name": r}} for r in reviewers]
        data = self.put(f"{self.API}{self._pr_path(project, repo)}/{pr_id}", json=payload).json()
        return PullRequest.model_validate(data)

    def merge_pull_request(
        self,
        project: str,
        repo: str,
        pr_id: int,
        *,
        version: int | None = None,
        strategy: str | None = None,
    ) -> PullRequest:
        """POST .../pull-requests/{id}/merge"""
        if version is None:
            version = self.get_pull_request(project, repo, pr_id).version
        query: dict[str, Any] = {"version": version}
        body: dict[str, Any] | None = None
        if strategy:
            body = {"strategyId": strategy}
        data = self.request(
            "POST",
            f"{self.API}{self._pr_path(project, repo)}/{pr_id}/merge",
            params=query,
            json=body,
        ).json()
        return PullRequest.model_validate(data)

    def decline_pull_request(
        self,
        project: str,
        repo: str,
        pr_id: int,
        *,
        version: int | None = None,
    ) -> PullRequest:
        """POST .../pull-requests/{id}/decline"""
        if version is None:
            version = self.get_pull_request(project, repo, pr_id).version
        data = self.post(
            f"{self.API}{self._pr_path(project, repo)}/{pr_id}/decline",
            json={"version": version},
        ).json()
        return PullRequest.model_validate(data)

    def approve_pull_request(self, project: str, repo: str, pr_id: int) -> PullRequestParticipant:
        """POST .../pull-requests/{id}/approve"""
        data = self.post(f"{self.API}{self._pr_path(project, repo)}/{pr_id}/approve").json()
        return PullRequestParticipant.model_validate(data)

    def unapprove_pull_request(self, project: str, repo: str, pr_id: int) -> None:
        """DELETE .../pull-requests/{id}/approve"""
        self.delete(f"{self.API}{self._pr_path(project, repo)}/{pr_id}/approve")

    def needs_work_pull_request(self, project: str, repo: str, pr_id: int) -> None:
        """PUT reviewer status to NEEDS_WORK for the current user."""
        slug = self._get_current_user_slug()
        self.put(
            f"{self.API}{self._pr_path(project, repo)}/{pr_id}/participants/{slug}",
            json={"status": "NEEDS_WORK"},
        )

    def reopen_pull_request(
        self,
        project: str,
        repo: str,
        pr_id: int,
        *,
        version: int | None = None,
    ) -> PullRequest:
        """POST .../pull-requests/{id}/reopen"""
        if version is None:
            version = self.get_pull_request(project, repo, pr_id).version
        data = self.post(
            f"{self.API}{self._pr_path(project, repo)}/{pr_id}/reopen",
            json={"version": version},
        ).json()
        return PullRequest.model_validate(data)

    # ------------------------------------------------------------------
    # Comments — Write (Phase 2)
    # ------------------------------------------------------------------

    def add_pull_request_comment(
        self,
        project: str,
        repo: str,
        pr_id: int,
        *,
        text: str,
        anchor: dict[str, Any] | None = None,
    ) -> PullRequestComment:
        """POST .../pull-requests/{id}/comments"""
        payload: dict[str, Any] = {"text": text}
        if anchor:
            payload["anchor"] = anchor
        data = self.post(
            f"{self.API}{self._pr_path(project, repo)}/{pr_id}/comments",
            json=payload,
        ).json()
        return PullRequestComment.model_validate(data)

    def reply_to_comment(
        self,
        project: str,
        repo: str,
        pr_id: int,
        comment_id: int,
        *,
        text: str,
    ) -> PullRequestComment:
        """POST .../pull-requests/{id}/comments with parent."""
        payload: dict[str, Any] = {
            "text": text,
            "parent": {"id": comment_id},
        }
        data = self.post(
            f"{self.API}{self._pr_path(project, repo)}/{pr_id}/comments",
            json=payload,
        ).json()
        return PullRequestComment.model_validate(data)

    # ------------------------------------------------------------------
    # Comments — CRUD (Phase 3)
    # ------------------------------------------------------------------

    def _get_comment(self, project: str, repo: str, pr_id: int, comment_id: int) -> dict[str, Any]:
        """Fetch a single comment (for version/text auto-fetch)."""
        data: dict[str, Any] = self.get(
            f"{self.API}{self._pr_path(project, repo)}/{pr_id}/comments/{comment_id}"
        ).json()
        return data

    def update_comment(
        self,
        project: str,
        repo: str,
        pr_id: int,
        comment_id: int,
        *,
        text: str,
        version: int | None = None,
        severity: str | None = None,
    ) -> PullRequestComment:
        """PUT .../comments/{id} — requires full text + version.

        `severity` (NORMAL/BLOCKER) is optional and lets a NORMAL comment be
        explicitly promoted to a task (or vice versa). It is independent of
        `resolve_comment`/`resolve_task` below.
        """
        if version is None:
            current = self._get_comment(project, repo, pr_id, comment_id)
            version = current.get("version", 0)
        payload: dict[str, Any] = {"text": text, "version": version}
        if severity:
            payload["severity"] = severity.upper()
        data = self.put(
            f"{self.API}{self._pr_path(project, repo)}/{pr_id}/comments/{comment_id}",
            json=payload,
        ).json()
        return PullRequestComment.model_validate(data)

    def delete_comment(
        self,
        project: str,
        repo: str,
        pr_id: int,
        comment_id: int,
        *,
        version: int | None = None,
    ) -> None:
        """DELETE .../comments/{id}?version=N"""
        if version is None:
            current = self._get_comment(project, repo, pr_id, comment_id)
            version = current.get("version", 0)
        self.delete(
            f"{self.API}{self._pr_path(project, repo)}/{pr_id}/comments/{comment_id}",
            params={"version": version},
        )

    def _put_comment_field(
        self,
        project: str,
        repo: str,
        pr_id: int,
        comment_id: int,
        *,
        field: str,
        value: Any,
        version: int | None,
        current: dict[str, Any] | None = None,
    ) -> dict[str, Any]:
        """PUT a single field (`state` or `threadResolved`) onto a comment.

        BBDC's comment PUT is a full-replace, so `text` and `version` from a
        live GET are always included alongside the target field. Optimistic
        locking: if `version` was auto-fetched (caller passed None) and the
        server returns 409, the comment is refetched once and the PUT retried
        with the fresh version. If the caller supplied an explicit `version`,
        a 409 is surfaced as-is (they asked for that exact version).
        """
        auto_version = version is None
        if current is None:
            current = self._get_comment(project, repo, pr_id, comment_id)
        v = version if version is not None else current.get("version", 0)
        payload: dict[str, Any] = {"text": current.get("text", ""), "version": v, field: value}
        url = f"{self.API}{self._pr_path(project, repo)}/{pr_id}/comments/{comment_id}"
        try:
            return self.put(url, json=payload).json()  # type: ignore[no-any-return]
        except ConflictError:
            if not auto_version:
                raise
            current = self._get_comment(project, repo, pr_id, comment_id)
            payload["version"] = current.get("version", 0)
            return self.put(url, json=payload).json()  # type: ignore[no-any-return]

    def resolve_comment(
        self,
        project: str,
        repo: str,
        pr_id: int,
        comment_id: int,
        *,
        version: int | None = None,
    ) -> PullRequestComment:
        """PUT .../comments/{id} with threadResolved=true.

        Flips the UI "Resolved" thread pill. Works on any comment regardless
        of severity, and does NOT touch `state` (which only means something on
        BLOCKER/task comments — see `resolve_task` for that). Verifies the
        server actually applied `threadResolved` before returning, since BBDC
        can silently ignore an unexpected field.
        """
        data = self._put_comment_field(
            project, repo, pr_id, comment_id, field="threadResolved", value=True, version=version
        )
        if data.get("threadResolved") is not True:
            raise ValidationError(
                f"Bitbucket did not mark comment {comment_id}'s thread as resolved "
                f"(server returned threadResolved={data.get('threadResolved')!r})"
            )
        return PullRequestComment.model_validate(data)

    def reopen_comment(
        self,
        project: str,
        repo: str,
        pr_id: int,
        comment_id: int,
        *,
        version: int | None = None,
    ) -> PullRequestComment:
        """PUT .../comments/{id} with threadResolved=false — reopens the thread.

        Does NOT touch `state`. See `resolve_comment` for details.
        """
        data = self._put_comment_field(
            project, repo, pr_id, comment_id, field="threadResolved", value=False, version=version
        )
        if data.get("threadResolved") is not False:
            raise ValidationError(
                f"Bitbucket did not reopen comment {comment_id}'s thread "
                f"(server returned threadResolved={data.get('threadResolved')!r})"
            )
        return PullRequestComment.model_validate(data)

    def resolve_task(
        self,
        project: str,
        repo: str,
        pr_id: int,
        comment_id: int,
        *,
        version: int | None = None,
    ) -> PullRequestComment:
        """PUT .../comments/{id} with state=RESOLVED — completes a task.

        Only valid on severity=BLOCKER comments (i.e. "tasks"). Does NOT touch
        `threadResolved` — completing a task doesn't flip the UI resolved pill.
        Raises ValidationError if the target comment is NORMAL.
        """
        current = self._get_comment(project, repo, pr_id, comment_id)
        severity = current.get("severity") or "NORMAL"
        if severity != "BLOCKER":
            raise ValidationError(
                f"comment {comment_id} is not a task (severity={severity}); "
                "use `comment resolve` to resolve the thread instead."
            )
        data = self._put_comment_field(
            project, repo, pr_id, comment_id, field="state", value="RESOLVED", version=version, current=current
        )
        if data.get("state") != "RESOLVED":
            raise ValidationError(
                f"Bitbucket did not mark task (comment {comment_id}) as resolved "
                f"(server returned state={data.get('state')!r})"
            )
        return PullRequestComment.model_validate(data)

    def reopen_task(
        self,
        project: str,
        repo: str,
        pr_id: int,
        comment_id: int,
        *,
        version: int | None = None,
    ) -> PullRequestComment:
        """PUT .../comments/{id} with state=OPEN — reopens a task.

        Only valid on severity=BLOCKER comments. Raises ValidationError if the
        target comment is NORMAL.
        """
        current = self._get_comment(project, repo, pr_id, comment_id)
        severity = current.get("severity") or "NORMAL"
        if severity != "BLOCKER":
            raise ValidationError(
                f"comment {comment_id} is not a task (severity={severity}); "
                "use `comment reopen` to reopen the thread instead."
            )
        data = self._put_comment_field(
            project, repo, pr_id, comment_id, field="state", value="OPEN", version=version, current=current
        )
        if data.get("state") != "OPEN":
            raise ValidationError(
                f"Bitbucket did not reopen task (comment {comment_id}) (server returned state={data.get('state')!r})"
            )
        return PullRequestComment.model_validate(data)

    # ------------------------------------------------------------------
    # Diff stat (Phase 3)
    # ------------------------------------------------------------------

    def get_pull_request_diffstat(self, project: str, repo: str, pr_id: int, *, limit: int = 100) -> list[DiffStat]:
        """GET .../pull-requests/{id}/changes — file-level change stats."""
        items = self._get_paged(
            f"{self._pr_path(project, repo)}/{pr_id}/changes",
            limit=limit,
        )
        return [DiffStat.model_validate(i) for i in items]

    # ------------------------------------------------------------------
    # Build status (Phase 3)
    # ------------------------------------------------------------------

    def get_build_statuses(self, commit_hash: str, *, limit: int = 25) -> list[BuildStatus]:
        """GET /rest/build-status/1.0/commits/{hash} — different API base."""
        items: list[dict[str, Any]] = []
        params: dict[str, Any] = {"limit": limit}
        while True:
            resp = self.get(f"/rest/build-status/1.0/commits/{commit_hash}", params=params)
            data = resp.json()
            items.extend(data.get("values", []))
            if data.get("isLastPage", True):
                break
            next_start = data.get("nextPageStart")
            if next_start is None:
                break
            params["start"] = next_start
        return [BuildStatus.model_validate(i) for i in items]

    # ------------------------------------------------------------------
    # Pending review (Phase 3)
    # ------------------------------------------------------------------

    def list_pull_requests_for_reviewer(self, *, state: str | None = None, limit: int = 25) -> list[PullRequest]:
        """GET /rest/api/1.0/inbox/pull-requests — PRs where current user is reviewer."""
        params: dict[str, Any] = {"limit": limit}
        if state:
            params["state"] = state.upper()
        try:
            resp = self.get(f"{self.API}/inbox/pull-requests", params=params)
            data = resp.json()
            return [PullRequest.model_validate(i) for i in data.get("values", [])]
        except NotFoundError:
            # Fallback for older server versions that lack the inbox API
            params_fb: dict[str, Any] = {"limit": limit, "role": "REVIEWER"}
            if state:
                params_fb["state"] = state.upper()
            resp = self.get(f"{self.API}/dashboard/pull-requests", params=params_fb)
            data = resp.json()
            return [PullRequest.model_validate(i) for i in data.get("values", [])]

    # ------------------------------------------------------------------
    # Tasks (Phase 3) — list via PR, CRUD via top-level /tasks
    # ------------------------------------------------------------------

    def list_tasks(self, project: str, repo: str, pr_id: int, *, limit: int = 25) -> list[Task]:
        """GET .../pull-requests/{id}/tasks"""
        items = self._get_paged(
            f"{self._pr_path(project, repo)}/{pr_id}/tasks",
            limit=limit,
        )
        return [Task.model_validate(i) for i in items]

    def get_task(self, task_id: int) -> Task:
        """GET /rest/api/1.0/tasks/{id}"""
        data = self.get(f"{self.API}/tasks/{task_id}").json()
        return Task.model_validate(data)

    def create_task(
        self,
        *,
        text: str,
        comment_id: int,
    ) -> Task:
        """POST /rest/api/1.0/tasks — anchored to a comment."""
        payload: dict[str, Any] = {
            "anchor": {"id": comment_id, "type": "COMMENT"},
            "text": text,
        }
        data = self.post(f"{self.API}/tasks", json=payload).json()
        return Task.model_validate(data)

    def update_task(self, task_id: int, *, state: str | None = None, text: str | None = None) -> Task:
        """PUT /rest/api/1.0/tasks/{id}"""
        payload: dict[str, Any] = {}
        if state:
            payload["state"] = state.upper()
        if text:
            payload["text"] = text
        data = self.put(f"{self.API}/tasks/{task_id}", json=payload).json()
        return Task.model_validate(data)

    def delete_task(self, task_id: int) -> None:
        """DELETE /rest/api/1.0/tasks/{id}"""
        self.delete(f"{self.API}/tasks/{task_id}")

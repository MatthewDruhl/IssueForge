"""The isolated-worktree workspace seam (US-4.2 / US-4.3).

A build must run against a freshly-fetched ``origin/<default>`` in a worktree that is *provably*
isolated from the developer's normal checkout, so a dirty checkout is never mutated and nothing
uncommitted is ever destroyed. This module fetches the default-branch sha, creates a detached
linked worktree under IssueForge's owned root, and proves isolation three ways (path outside the
checkout, ``HEAD`` byte-identical, index byte-identical) with preserve-on-failure recovery. It also
carries ``reset_worktree`` (extracted from MARVIN's ``build_recovery``), which reset --hards a
worktree ONLY to a verified commit sha — never a branch name, tag, tree, or nonexistent ref.

Isolation is a *git* boundary, distinct from the IO write seam: these subprocess git operations go
through ``issueforge.process.run`` (argv array, no shell, timeout, persisted invocation evidence).
"""

from __future__ import annotations

import os
import threading
import time
import uuid
from dataclasses import dataclass
from pathlib import Path

from issueforge import process
from issueforge.paths import state_root

_GIT_TIMEOUT = 120.0

# The add/remove of a linked worktree mutates the shared ``.git/worktrees`` metadata, so every
# creation is serialized process-wide; an unserialized ``git worktree add`` race corrupts it.
_ADD_LOCK = threading.Lock()
_SEQ_LOCK = threading.Lock()
_SEQ = 0


@dataclass(frozen=True)
class FetchResult:
    """The outcome of fetching the default-branch sha: ``ok`` gates everything downstream."""

    sha: str | None
    ok: bool
    reason: str | None


@dataclass(frozen=True)
class WorktreeResult:
    """A worktree-creation result. ``ok`` AND ``isolated`` must both hold to be usable."""

    path: Path | None
    ok: bool
    isolated: bool
    reason: str | None


def _git(args: list[str], *, cwd: Path) -> process.CommandResult:
    return process.run(["git", *args], cwd=Path(cwd), timeout=_GIT_TIMEOUT)


def _next_seq() -> int:
    global _SEQ
    with _SEQ_LOCK:
        _SEQ += 1
        return _SEQ


def _is_inside(child: Path, parent: Path) -> bool:
    child, parent = Path(child).resolve(), Path(parent).resolve()
    return child == parent or parent in child.parents


def _default_branch(checkout: Path) -> str | None:
    """Discover the remote default branch (never hardcode 'main')."""
    symbolic = _git(
        ["-C", str(checkout), "symbolic-ref", "--quiet", "refs/remotes/origin/HEAD"],
        cwd=checkout,
    )
    if symbolic.returncode == 0 and symbolic.stdout.strip():
        return symbolic.stdout.strip().rsplit("/", 1)[-1]
    abbrev = _git(["-C", str(checkout), "rev-parse", "--abbrev-ref", "origin/HEAD"], cwd=checkout)
    if abbrev.returncode == 0 and "/" in abbrev.stdout:
        return abbrev.stdout.strip().split("/", 1)[1]
    return None


def fetch_default_sha(checkout: Path) -> FetchResult:
    """Fetch the current tip of ``origin/<default>`` WITHOUT moving the checkout's own HEAD.

    A failed fetch (unreachable origin, unknown default branch) pauses: ``ok=False``, ``sha=None``,
    a non-empty reason, and no side effect on the checkout. On success the sha is the just-fetched
    remote tip (via ``FETCH_HEAD``), not a possibly-stale local ref.
    """
    checkout = Path(checkout)
    branch = _default_branch(checkout)
    if branch is None:
        return FetchResult(sha=None, ok=False, reason="could not resolve origin default branch")
    fetched = _git(["-C", str(checkout), "fetch", "origin", branch], cwd=checkout)
    if fetched.returncode != 0:
        return FetchResult(sha=None, ok=False, reason=f"git fetch failed: {fetched.stderr.strip()}")
    resolved = _git(["-C", str(checkout), "rev-parse", "FETCH_HEAD"], cwd=checkout)
    if resolved.returncode != 0 or not resolved.stdout.strip():
        return FetchResult(sha=None, ok=False, reason="could not resolve FETCH_HEAD after fetch")
    return FetchResult(sha=resolved.stdout.strip(), ok=True, reason=None)


def _snapshot(checkout: Path) -> tuple[bytes, bytes, str]:
    """The checkout's ``.git/HEAD`` bytes, ``.git/index`` bytes, and resolved HEAD sha."""
    git_dir = Path(checkout) / ".git"
    head = (git_dir / "HEAD").read_bytes() if (git_dir / "HEAD").exists() else b""
    index = (git_dir / "index").read_bytes() if (git_dir / "index").exists() else b""
    rev = _git(["-C", str(checkout), "rev-parse", "HEAD"], cwd=checkout).stdout.strip()
    return head, index, rev


def _worktree_name(stamp: str | None, pid: int | None) -> str:
    """A name carrying BOTH a timestamp and a pid (the agent-contract caveat), plus a per-call
    sequence + uuid so two creations in the same second with the same pid never collide."""
    stamp = stamp or time.strftime("%Y%m%dT%H%M%S")
    pid = pid if pid is not None else os.getpid()
    return f"if-wt-{stamp}-{pid}-{_next_seq()}-{uuid.uuid4().hex[:8]}"


def _default_add(checkout_dir: Path, worktree_path: Path, target_sha: str) -> None:
    """Create a DETACHED linked worktree at ``target_sha`` — detached so no branch exists to
    accidentally delete on teardown."""
    _git(
        ["-C", str(checkout_dir), "worktree", "add", "--detach", str(worktree_path), target_sha],
        cwd=checkout_dir,
    )


def create_isolated_worktree(
    checkout: Path,
    sha: str,
    *,
    worktrees_root: Path | None = None,
    add=None,
    stamp: str | None = None,
    pid: int | None = None,
) -> WorktreeResult:
    """Create a detached worktree at ``sha`` and PROVE it is isolated from the checkout.

    The proof has three independent legs — the worktree path is outside the checkout tree, the
    checkout's ``HEAD`` bytes are unchanged, and the checkout's index bytes are unchanged. Any leg
    failing yields ``ok=False, isolated=False`` and never infers safety. Recovery is preserve-only:
    a partially-created worktree (with its uncommitted content) is left in place, never
    force-removed, and the checkout's uncommitted work is never reset or cleaned.
    """
    checkout = Path(checkout)
    root = Path(worktrees_root) if worktrees_root is not None else Path(state_root()) / "worktrees"
    worktree_path = root / _worktree_name(stamp, pid)
    add = add or _default_add

    # Path-outside leg is checked BEFORE any creation: a worktree inside the checkout tree can never
    # be isolated, so we never proceed to mutate anything.
    if _is_inside(worktree_path, checkout):
        return WorktreeResult(
            path=worktree_path,
            ok=False,
            isolated=False,
            reason=f"worktree path {worktree_path} is inside the checkout tree",
        )

    with _ADD_LOCK:
        head_before, index_before, rev_before = _snapshot(checkout)
        try:
            add(checkout, worktree_path, sha)
        except Exception as exc:  # noqa: BLE001 — preserve, report, never destroy the checkout.
            return WorktreeResult(
                path=worktree_path,
                ok=False,
                isolated=False,
                reason=f"worktree creation raised: {exc!r}",
            )
        head_after, index_after, rev_after = _snapshot(checkout)

    path_outside = not _is_inside(worktree_path, checkout)
    head_same = head_after == head_before and rev_after == rev_before
    index_same = index_after == index_before
    isolated = path_outside and head_same and index_same
    if not isolated:
        reasons = []
        if not path_outside:
            reasons.append("path inside checkout")
        if not head_same:
            reasons.append("checkout HEAD changed during creation")
        if not index_same:
            reasons.append("checkout index changed during creation")
        # Preserve-on-failure: the partial worktree (if one was made) is left intact — no
        # `worktree remove --force`, no reset --hard — so uncommitted work is never destroyed.
        return WorktreeResult(
            path=worktree_path, ok=False, isolated=False, reason="; ".join(reasons)
        )
    return WorktreeResult(path=worktree_path, ok=True, isolated=True, reason=None)


def reset_worktree(worktree: Path, base_sha: str) -> None:
    """``reset --hard`` a worktree to ``base_sha`` — but ONLY after proving it is a real COMMIT sha.

    A branch name, a tag, a tree sha, or a nonexistent ref all RAISE before any reset runs, so a
    typo can never silently reset the tree to whatever a name currently points at. The dirty
    working tree is preserved on rejection; the reset is ``git -C`` scoped to the worktree.
    """
    worktree = Path(worktree)
    verified = _git(
        ["-C", str(worktree), "rev-parse", "--verify", "--quiet", base_sha], cwd=worktree
    )
    if verified.returncode != 0 or not verified.stdout.strip():
        raise ValueError(f"reset base {base_sha!r} does not resolve to any object")
    # Identity check: a symbolic name (branch/tag) resolves to a sha that differs from the input,
    # so only a raw object id passes. This is what rejects 'main' / 'v1-tag'.
    if verified.stdout.strip() != base_sha:
        raise ValueError(f"reset base {base_sha!r} is a symbolic ref, not a commit sha")
    kind = _git(["-C", str(worktree), "cat-file", "-t", base_sha], cwd=worktree)
    if kind.stdout.strip() != "commit":
        raise ValueError(f"reset base {base_sha!r} is a {kind.stdout.strip()!r}, not a commit")
    _git(["-C", str(worktree), "reset", "--hard", base_sha], cwd=worktree)

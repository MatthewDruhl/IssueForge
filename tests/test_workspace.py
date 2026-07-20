"""Committed PENDING acceptance suite for #12 (S6) — the isolated-worktree workspace seam.

Owns US-4.2 (fetch + separate worktree from verified ``origin/<default>``), US-4.3 (a dirty
normal checkout is permitted ONLY when isolation is proven — path outside, HEAD byte-identical,
index byte-identical), the "nothing uncommitted is ever destroyed" recovery discipline, the
timestamp+PID worktree naming caveat, and ``reset_worktree`` extracted from build_recovery.

``issueforge.workspace`` does not exist yet, so every test imports it inside its body and is
``@pytest.mark.xfail(strict=True, reason="PENDING (#12)")``. The git helpers below touch no
IssueForge code and build a REAL, offline, fetchable ``origin`` (a bare repo) plus a checkout
cloned from it, so ``git fetch origin`` retrieves genuine commits — never a mock.
"""

from __future__ import annotations

import os
import subprocess
import threading
from pathlib import Path

import pytest

_GIT_ID = ["-c", "user.name=IF Tests", "-c", "user.email=tests@issueforge.invalid"]


def _git(repo: Path, *args: str, check: bool = True) -> subprocess.CompletedProcess:
    return subprocess.run(
        ["git", "-C", str(repo), *_GIT_ID, *args], check=check, capture_output=True, text=True
    )


def _bare_origin_with_checkout(root: Path, *, branch: str = "main") -> tuple[Path, Path]:
    """A bare ``origin`` (one commit on ``branch``) plus a checkout cloned from it (offline fetch)."""
    origin = root / "origin.git"
    subprocess.run(["git", "init", "-q", "--bare", "-b", branch, str(origin)], check=True)
    seed = root / "seed"
    subprocess.run(["git", "init", "-q", "-b", branch, str(seed)], check=True)
    (seed / "README.md").write_text("seed\n")
    _git(seed, "add", "README.md")
    _git(seed, "commit", "-qm", "seed")
    _git(seed, "remote", "add", "origin", str(origin))
    _git(seed, "push", "-q", "origin", branch)
    checkout = root / "checkout"
    subprocess.run(["git", "clone", "-q", str(origin), str(checkout)], check=True)
    return origin, checkout


def _advance_origin(root: Path, origin: Path, *, branch: str = "main", text: str = "next") -> str:
    """Commit a new file to ``origin`` via a throwaway clone; return the new tip sha."""
    work = root / f"push-{text}"
    subprocess.run(["git", "clone", "-q", str(origin), str(work)], check=True)
    (work / f"{text}.txt").write_text(text + "\n")
    _git(work, "add", f"{text}.txt")
    _git(work, "commit", "-qm", text)
    _git(work, "push", "-q", "origin", branch)
    return _git(work, "rev-parse", "HEAD").stdout.strip()


def _head_index_rev(checkout: Path) -> tuple[bytes, bytes, str]:
    """Snapshot the checkout's ``.git/HEAD`` bytes, ``.git/index`` bytes, and resolved HEAD sha."""
    git_dir = checkout / ".git"
    head = (git_dir / "HEAD").read_bytes()
    index_path = git_dir / "index"
    index = index_path.read_bytes() if index_path.exists() else b""
    return head, index, _git(checkout, "rev-parse", "HEAD").stdout.strip()


def _is_inside(child: Path, parent: Path) -> bool:
    child, parent = child.resolve(), parent.resolve()
    return child == parent or parent in child.parents


def _worktree_list(checkout: Path) -> str:
    return _git(checkout, "worktree", "list").stdout


# ----------------------------------------------------------------------- fetch (US-4.2)


def test_worktree_head_is_the_freshly_fetched_origin_sha_on_a_non_main_branch(tmp_path):
    """The worktree is based on the sha JUST fetched from origin/<default>, not a local ref — and
    the default branch is discovered, not hardcoded to 'main'.

    technical (contract): a checkout cloned from an origin whose default branch is 'trunk'; origin
    then advances by one commit (NEXT). fetch_default_sha(checkout) -> ok=True, sha=NEXT while the
    checkout's own HEAD still points at the old sha. create_isolated_worktree(checkout, NEXT) ->
    a worktree whose HEAD == NEXT.
    """
    from issueforge import workspace

    origin, checkout = _bare_origin_with_checkout(tmp_path, branch="trunk")
    local_before = _git(checkout, "rev-parse", "HEAD").stdout.strip()
    new_sha = _advance_origin(tmp_path, origin, branch="trunk")
    assert new_sha != local_before

    fetched = workspace.fetch_default_sha(checkout)
    assert fetched.ok is True and fetched.sha == new_sha
    assert _git(checkout, "rev-parse", "HEAD").stdout.strip() == local_before

    result = workspace.create_isolated_worktree(checkout, fetched.sha)
    assert result.ok is True
    assert _git(result.path, "rev-parse", "HEAD").stdout.strip() == new_sha


def test_failed_fetch_pauses_and_adds_no_worktree(tmp_path):
    """A failed fetch pauses (ok=False) and adds no worktree — the worktree list is unchanged.

    technical (contract): point origin at a nonexistent path; snapshot `git worktree list`;
    fetch_default_sha(checkout) -> ok=False, sha=None, non-empty reason; and the worktree list is
    byte-identical afterwards (no worktree was created on a failed read).
    """
    from issueforge import workspace

    _origin, checkout = _bare_origin_with_checkout(tmp_path)
    _git(checkout, "remote", "set-url", "origin", str(tmp_path / "does-not-exist.git"))
    worktrees_before = _worktree_list(checkout)

    fetched = workspace.fetch_default_sha(checkout)
    assert fetched.ok is False and fetched.sha is None and fetched.reason
    assert _worktree_list(checkout) == worktrees_before


# ----------------------------------------------- isolation proof (US-4.2 / US-4.3)


def test_isolation_proof_all_three_hold_after_a_real_worktree_creation(tmp_path):
    """Isolation proof — all three: worktree outside the checkout tree, checkout HEAD byte-identical,
    checkout index byte-identical.

    technical (contract): snapshot (.git/HEAD bytes, .git/index bytes, rev) before; the REAL
    create_isolated_worktree(checkout, sha) -> ok, isolated. Then result.path is not inside the
    checkout tree, and .git/HEAD bytes, .git/index bytes, and `git rev-parse HEAD` are all unchanged.
    """
    from issueforge import workspace

    _origin, checkout = _bare_origin_with_checkout(tmp_path)
    sha = _git(checkout, "rev-parse", "HEAD").stdout.strip()
    head_b, index_b, rev_b = _head_index_rev(checkout)

    result = workspace.create_isolated_worktree(checkout, sha)
    assert result.ok is True and result.isolated is True

    head_a, index_a, rev_a = _head_index_rev(checkout)
    assert not _is_inside(result.path, checkout)
    assert head_a == head_b and index_a == index_b and rev_a == rev_b


def test_dirty_checkout_is_permitted_and_all_three_proofs_still_hold(tmp_path):
    """US-4.3: a dirty normal checkout is allowed, but ONLY because all three isolation proofs still
    hold, and the uncommitted change is preserved.

    technical (contract): write an uncommitted edit into the checkout; snapshot HEAD/index bytes;
    create_isolated_worktree(checkout, sha) -> ok, isolated. Afterwards: worktree outside the tree,
    .git/HEAD bytes unchanged, .git/index bytes unchanged, the dirty file content intact, and
    `git status --porcelain` still reports the checkout dirty.
    """
    from issueforge import workspace

    _origin, checkout = _bare_origin_with_checkout(tmp_path)
    sha = _git(checkout, "rev-parse", "HEAD").stdout.strip()
    (checkout / "README.md").write_text("LOCALLY DIRTY — do not lose\n")
    head_b, index_b, _ = _head_index_rev(checkout)

    result = workspace.create_isolated_worktree(checkout, sha)
    assert result.ok is True and result.isolated is True

    head_a, index_a, _ = _head_index_rev(checkout)
    assert not _is_inside(result.path, checkout)
    assert head_a == head_b and index_a == index_b
    assert (checkout / "README.md").read_text() == "LOCALLY DIRTY — do not lose\n"
    assert _git(checkout, "status", "--porcelain").stdout.strip() != ""


def test_a_benign_custom_creator_that_truly_isolates_succeeds(tmp_path):
    """A custom creator that produces a genuinely isolated worktree must SUCCEED — the proof does
    not reject every non-default creator, only ones that break isolation.

    technical (contract): an injected `add` that delegates to real `git worktree add --detach`;
    create_isolated_worktree(checkout, sha, add=benign) -> ok=True, isolated=True.
    """
    from issueforge import workspace

    _origin, checkout = _bare_origin_with_checkout(tmp_path)
    sha = _git(checkout, "rev-parse", "HEAD").stdout.strip()

    def _benign_add(checkout_dir, worktree_path, target_sha):
        _git(Path(checkout_dir), "worktree", "add", "--detach", str(worktree_path), target_sha)

    result = workspace.create_isolated_worktree(checkout, sha, add=_benign_add)
    assert result.ok is True and result.isolated is True


def test_index_mutation_during_creation_is_caught_as_unprovable_isolation(tmp_path):
    """An index mutation during creation fails the proof (isolated=False) — it is never inferred safe.

    technical (contract): an injected `add` that stages a file into the checkout's MAIN index;
    create_isolated_worktree re-reads the index, sees it changed -> ok=False, isolated=False,
    non-empty reason.
    """
    from issueforge import workspace

    _origin, checkout = _bare_origin_with_checkout(tmp_path)
    sha = _git(checkout, "rev-parse", "HEAD").stdout.strip()

    def _index_mutating_add(checkout_dir, worktree_path, target_sha):
        (Path(checkout_dir) / "sneaked.txt").write_text("x\n")
        _git(Path(checkout_dir), "add", "sneaked.txt")

    result = workspace.create_isolated_worktree(checkout, sha, add=_index_mutating_add)
    assert result.ok is False and result.isolated is False and result.reason


def test_head_mutation_during_creation_is_caught_as_unprovable_isolation(tmp_path):
    """A HEAD move during creation fails the proof — the proof checks HEAD, not only the index.

    technical (contract): an injected `add` that makes a commit in the checkout (moving HEAD);
    create_isolated_worktree re-reads HEAD, sees it moved -> ok=False, isolated=False.
    """
    from issueforge import workspace

    _origin, checkout = _bare_origin_with_checkout(tmp_path)
    sha = _git(checkout, "rev-parse", "HEAD").stdout.strip()

    def _head_mutating_add(checkout_dir, worktree_path, target_sha):
        # Create a REAL isolated worktree (so a path-only check would be satisfied), THEN move the
        # checkout's HEAD — only a real HEAD-proof catches this.
        _git(Path(checkout_dir), "worktree", "add", "--detach", str(worktree_path), target_sha)
        _git(Path(checkout_dir), "commit", "--allow-empty", "-qm", "moved HEAD")

    result = workspace.create_isolated_worktree(checkout, sha, add=_head_mutating_add)
    assert result.ok is False and result.isolated is False


def test_a_worktree_inside_the_checkout_tree_fails_the_path_proof(tmp_path):
    """A worktree placed inside the checkout's working tree fails the path-outside proof.

    technical (contract): create_isolated_worktree with a worktrees_root INSIDE the checkout ->
    ok=False / isolated=False (the path-outside leg of the proof fails), never proceeding.
    """
    from issueforge import workspace

    _origin, checkout = _bare_origin_with_checkout(tmp_path)
    sha = _git(checkout, "rev-parse", "HEAD").stdout.strip()

    result = workspace.create_isolated_worktree(checkout, sha, worktrees_root=checkout / "inside")
    assert result.ok is False and result.isolated is False


# ------------------------------------------- preserve on failure (recovery discipline)


def test_a_dirty_partial_worktree_is_preserved_not_force_removed_when_isolation_fails(tmp_path):
    """Failure & recovery: when a REAL worktree was partially created and then isolation cannot be
    proven, the partial worktree (with its uncommitted content) is preserved, never force-removed.

    technical (contract): an injected `add` that (1) creates a real detached worktree, (2) writes a
    tracked and an untracked change into it, then (3) trips isolation by dirtying the checkout's
    main index. create_isolated_worktree -> ok=False; the partial worktree still EXISTS with its
    untracked file intact, and result.path/reason report it (no `worktree remove --force`).
    """
    from issueforge import workspace

    _origin, checkout = _bare_origin_with_checkout(tmp_path)
    sha = _git(checkout, "rev-parse", "HEAD").stdout.strip()
    made = {}

    def _partial_then_break(checkout_dir, worktree_path, target_sha):
        _git(Path(checkout_dir), "worktree", "add", "--detach", str(worktree_path), target_sha)
        (Path(worktree_path) / "untracked_work.txt").write_text("do not lose me\n")
        (Path(worktree_path) / "README.md").write_text("tracked edit\n")
        made["path"] = Path(worktree_path)
        # Trip isolation AFTER the worktree exists, so cleanup must decide whether to force-remove.
        (Path(checkout_dir) / "sneak.txt").write_text("x\n")
        _git(Path(checkout_dir), "add", "sneak.txt")

    result = workspace.create_isolated_worktree(checkout, sha, add=_partial_then_break)
    assert result.ok is False
    assert made["path"].exists()
    # BOTH the untracked file AND the tracked edit survive — a reset --hard would restore README
    # to committed content and silently destroy the tracked edit while keeping the untracked file.
    assert (made["path"] / "untracked_work.txt").read_text() == "do not lose me\n"
    assert (made["path"] / "README.md").read_text() == "tracked edit\n"


def test_nothing_uncommitted_in_the_checkout_is_destroyed_when_creation_raises(tmp_path):
    """A creation that raises preserves the checkout's uncommitted work — no reset --hard, no
    clean -fd on the checkout.

    technical (contract): the checkout has a tracked edit AND an untracked file; an injected `add`
    that raises. create_isolated_worktree -> ok=False; the tracked edit and untracked file are
    intact and `git status --porcelain` is unchanged from before the call.
    """
    from issueforge import workspace

    _origin, checkout = _bare_origin_with_checkout(tmp_path)
    sha = _git(checkout, "rev-parse", "HEAD").stdout.strip()
    (checkout / "README.md").write_text("uncommitted edit\n")
    (checkout / "untracked.txt").write_text("untracked work\n")
    status_before = _git(checkout, "status", "--porcelain").stdout

    def _raising_add(checkout_dir, worktree_path, target_sha):
        raise RuntimeError("creation blew up")

    result = workspace.create_isolated_worktree(checkout, sha, add=_raising_add)
    assert result.ok is False
    assert (checkout / "README.md").read_text() == "uncommitted edit\n"
    assert (checkout / "untracked.txt").read_text() == "untracked work\n"
    assert _git(checkout, "status", "--porcelain").stdout == status_before


# ---------------------------------------------------- worktree naming (timestamp + PID)


def test_worktree_name_carries_the_injected_timestamp_and_pid(tmp_path):
    """The worktree name is suffixed with timestamp PLUS pid, and two different pids in the same
    second yield different names (the agent-contract caveat carried as a requirement).

    technical (contract): create_isolated_worktree(checkout, sha, stamp='20260720T000000',
    pid=111) -> a path whose name contains both '20260720T000000' and '111'. A second call with
    the SAME stamp but pid=222 -> a different path containing '222'. A uuid-only name (containing
    neither) fails this contract.
    """
    from issueforge import workspace

    _origin, checkout = _bare_origin_with_checkout(tmp_path)
    sha = _git(checkout, "rev-parse", "HEAD").stdout.strip()

    first = workspace.create_isolated_worktree(checkout, sha, stamp="20260720T000000", pid=111)
    second = workspace.create_isolated_worktree(checkout, sha, stamp="20260720T000000", pid=222)
    assert first.ok is True and second.ok is True
    assert "20260720T000000" in first.path.name and "111" in first.path.name
    assert "222" in second.path.name
    assert first.path != second.path


def test_two_rapid_creations_get_distinct_worktree_paths(tmp_path):
    """Two worktrees created back-to-back in the same process get DISTINCT paths — a same-second
    collision is impossible even with the same pid.

    technical (contract): two default create_isolated_worktree calls (same pid, near-identical
    wall clock) -> both ok and first.path != second.path.
    """
    from issueforge import workspace

    _origin, checkout = _bare_origin_with_checkout(tmp_path)
    sha = _git(checkout, "rev-parse", "HEAD").stdout.strip()

    # Same process (same pid), near-identical wall clock — the two names must still differ.
    first = workspace.create_isolated_worktree(checkout, sha)
    second = workspace.create_isolated_worktree(checkout, sha)
    assert first.ok is True and second.ok is True
    assert first.path != second.path


def test_the_created_worktree_is_detached_so_no_branch_exists_to_delete(tmp_path):
    """The worktree is created DETACHED at the verified origin sha (not on a branch), so the
    'worktree remove must not delete a branch' caveat is satisfied by construction.

    technical (contract): create_isolated_worktree(checkout, sha) -> the worktree is in
    detached-HEAD state (`git symbolic-ref -q HEAD` fails) at exactly `sha`.
    """
    from issueforge import workspace

    _origin, checkout = _bare_origin_with_checkout(tmp_path)
    sha = _git(checkout, "rev-parse", "HEAD").stdout.strip()

    result = workspace.create_isolated_worktree(checkout, sha)
    assert result.ok is True
    symref = _git(result.path, "symbolic-ref", "-q", "HEAD", check=False)
    assert symref.returncode != 0  # detached: no symbolic HEAD → no branch
    assert _git(result.path, "rev-parse", "HEAD").stdout.strip() == sha


def test_concurrent_worktree_creation_is_serialized_and_uncorrupted(tmp_path):
    """The add/remove bracketing is serialized: several worktrees created concurrently from the
    same checkout all succeed with distinct valid paths and leave ``.git/worktrees`` uncorrupted.

    technical (contract): start N threads behind a barrier so their create_isolated_worktree calls
    race; every result is ok with a distinct path, each worktree resolves to the fetched sha, and
    `git worktree list` afterwards enumerates all of them without error (an unserialized
    `git worktree add` would corrupt the shared .git/worktrees metadata).
    """
    from issueforge import workspace

    _origin, checkout = _bare_origin_with_checkout(tmp_path)
    sha = _git(checkout, "rev-parse", "HEAD").stdout.strip()

    n = 5
    barrier = threading.Barrier(n)
    results: list = []
    lock = threading.Lock()

    def _worker():
        barrier.wait()
        r = workspace.create_isolated_worktree(checkout, sha)
        with lock:
            results.append(r)

    threads = [threading.Thread(target=_worker) for _ in range(n)]
    for t in threads:
        t.start()
    for t in threads:
        t.join()

    assert len(results) == n
    assert all(r.ok for r in results)
    paths = [r.path for r in results]
    assert len({str(p) for p in paths}) == n
    for p in paths:
        assert _git(p, "rev-parse", "HEAD").stdout.strip() == sha
    # The shared metadata is intact — listing does not error and sees every worktree.
    listing = _git(checkout, "worktree", "list").stdout
    assert listing.count("\n") >= n


# ------------------------------------------- reset_worktree (extracted from build_recovery)


def test_reset_worktree_resets_a_dirty_worktree_to_committed_content(tmp_path):
    """reset_worktree with a valid commit sha actually resets --hard (not a no-op): a dirty tracked
    edit is discarded and the file returns to its committed content.

    technical (contract): a worktree with README committed as 'seed\\n' and a working-tree edit to
    'mid-attempt edit\\n'; reset_worktree(worktree, <base_commit_sha>) -> HEAD equals the sha AND
    README is back to the committed 'seed\\n' (proving the reset ran, not that HEAD merely already
    matched).
    """
    from issueforge import workspace

    _origin, checkout = _bare_origin_with_checkout(tmp_path)
    sha = _git(checkout, "rev-parse", "HEAD").stdout.strip()
    worktree = workspace.create_isolated_worktree(checkout, sha).path
    assert (worktree / "README.md").read_text() == "seed\n"
    (worktree / "README.md").write_text("mid-attempt edit\n")

    workspace.reset_worktree(worktree, sha)
    assert _git(worktree, "rev-parse", "HEAD").stdout.strip() == sha
    assert (worktree / "README.md").read_text() == "seed\n"


def test_reset_worktree_refuses_a_branch_name_and_a_tag(tmp_path):
    """reset_worktree requires a COMMIT sha, not a symbolic ref: a branch NAME and a TAG are both
    rejected (a typo cannot silently reset to whatever a name currently points at).

    technical (contract): reset_worktree(worktree, 'main') RAISES (a branch name, not a sha);
    reset_worktree(worktree, 'v1-tag') RAISES (a tag); the worktree HEAD is unchanged after each.
    """
    from issueforge import workspace

    _origin, checkout = _bare_origin_with_checkout(tmp_path)
    sha = _git(checkout, "rev-parse", "HEAD").stdout.strip()
    worktree = workspace.create_isolated_worktree(checkout, sha).path
    _git(checkout, "tag", "v1-tag", sha)

    head_before = _git(worktree, "rev-parse", "HEAD").stdout.strip()
    for ref in ("main", "v1-tag"):
        with pytest.raises(Exception):
            workspace.reset_worktree(worktree, ref)
        assert _git(worktree, "rev-parse", "HEAD").stdout.strip() == head_before


def test_reset_worktree_refuses_a_tree_sha_and_preserves_dirty_tracked_bytes(tmp_path):
    """reset_worktree verifies its base resolves to a COMMIT before reset --hard; a TREE sha
    (resolvable as an object but not a commit) is rejected and nothing is reset.

    technical (contract): base = `git rev-parse HEAD^{tree}` (a real tree object, not a commit).
    reset_worktree(worktree, tree_sha) RAISES; the worktree's HEAD is unchanged and a tracked
    uncommitted edit is preserved byte-for-byte — a typo cannot silently reset to the wrong object,
    and it never resets first and validates after.
    """
    from issueforge import workspace

    _origin, checkout = _bare_origin_with_checkout(tmp_path)
    sha = _git(checkout, "rev-parse", "HEAD").stdout.strip()
    worktree = workspace.create_isolated_worktree(checkout, sha).path
    (worktree / "README.md").write_text("precious tracked edit\n")
    tree_sha = _git(worktree, "rev-parse", "HEAD^{tree}").stdout.strip()

    head_before = _git(worktree, "rev-parse", "HEAD").stdout.strip()
    with pytest.raises(Exception):
        workspace.reset_worktree(worktree, tree_sha)
    assert _git(worktree, "rev-parse", "HEAD").stdout.strip() == head_before
    assert (worktree / "README.md").read_text() == "precious tracked edit\n"


def test_reset_worktree_refuses_a_nonexistent_ref(tmp_path):
    """reset_worktree rejects a ref that resolves to nothing, rather than resetting to an
    unexpected object.

    technical (contract): reset_worktree(worktree, 'no-such-ref-xyz') RAISES and the worktree HEAD
    is unchanged.
    """
    from issueforge import workspace

    _origin, checkout = _bare_origin_with_checkout(tmp_path)
    sha = _git(checkout, "rev-parse", "HEAD").stdout.strip()
    worktree = workspace.create_isolated_worktree(checkout, sha).path

    head_before = _git(worktree, "rev-parse", "HEAD").stdout.strip()
    with pytest.raises(Exception):
        workspace.reset_worktree(worktree, "no-such-ref-xyz")
    assert _git(worktree, "rev-parse", "HEAD").stdout.strip() == head_before


# ======================================================================
# Issue #58 — S6 hardening: adversarial tests reproducing the 14 Codex-found
# defects the happy-path S6 suite missed. Each is PENDING until the #58 build.
# ======================================================================


# ===== #58 defect #3 =====
@pytest.mark.xfail(strict=True, reason="PENDING (#58)")
def test_default_branch_with_slash_resolved_fully_not_last_segment(tmp_path):
    """A default branch whose name contains a slash (e.g. 'release/v1') must resolve to the WHOLE
    branch name, not just the segment after the last slash, or the fetch targets a branch that does
    not exist and the build can never start.

    technical (contract): an origin whose default branch is 'release/v1'
    (origin/HEAD -> refs/remotes/origin/release/v1); origin then advances that branch by one commit
    (NEXT). fetch_default_sha(checkout) must strip exactly the 'refs/remotes/origin/' prefix and
    fetch 'release/v1' -> ok=True, sha=NEXT (the just-fetched tip), while the checkout's own HEAD is
    unmoved; a worktree created at that sha has HEAD == NEXT. The current impl rsplits on '/',
    resolves the branch to 'v1', fetches a nonexistent branch, and returns ok=False / sha=None.
    """
    from issueforge import workspace

    origin, checkout = _bare_origin_with_checkout(tmp_path, branch="release/v1")
    # origin/HEAD points at the slash-bearing default branch, as `git clone` records it.
    symref = _git(checkout, "symbolic-ref", "refs/remotes/origin/HEAD")
    assert symref.stdout.strip() == "refs/remotes/origin/release/v1"

    local_before = _git(checkout, "rev-parse", "HEAD").stdout.strip()
    new_sha = _advance_origin(tmp_path, origin, branch="release/v1")
    assert new_sha != local_before

    fetched = workspace.fetch_default_sha(checkout)
    assert fetched.ok is True and fetched.sha == new_sha
    assert _git(checkout, "rev-parse", "HEAD").stdout.strip() == local_before

    result = workspace.create_isolated_worktree(checkout, fetched.sha)
    assert result.ok is True
    assert _git(result.path, "rev-parse", "HEAD").stdout.strip() == new_sha


# ===== #58 defect #4 =====
@pytest.mark.xfail(strict=True, reason="PENDING (#58)")
def test_worktree_creation_fails_when_add_is_a_noop(tmp_path):
    """Creation must PROVE the worktree exists — an unchanged checkout snapshot is necessary but
    not sufficient. When the ``add`` seam is a no-op (git failed, or added nothing), no worktree was
    created, so create_isolated_worktree must return ok=False, never infer success from the fact
    that the checkout was left untouched.

    technical (contract): with an injected ``add`` that does nothing, create_isolated_worktree ->
    ok=False, isolated=False, non-empty reason (today ``_default_add`` discards git's return code
    and the unchanged before/after snapshot yields ok=True, isolated=True — the defect). A positive
    control with the real default creator must still succeed AND be provably a registered, detached
    linked worktree at exactly ``sha`` (path exists, appears in ``git worktree list``, ``symbolic-ref
    -q HEAD`` fails, ``rev-parse HEAD`` == sha) so the fix cannot be a lazy always-False.
    """
    from issueforge import workspace

    _origin, checkout = _bare_origin_with_checkout(tmp_path)
    sha = _git(checkout, "rev-parse", "HEAD").stdout.strip()

    # A no-op add: creates no worktree, so the checkout snapshot is unchanged — the exact condition
    # today's impl mistakes for proven isolation.
    def _noop_add(checkout_dir, worktree_path, target_sha):
        return None

    result = workspace.create_isolated_worktree(
        checkout, sha, worktrees_root=tmp_path / "wtroot", add=_noop_add
    )
    assert result.ok is False
    assert result.isolated is False
    assert result.reason

    # Positive control: the real creator succeeds and is a proven registered/detached worktree at sha.
    good = workspace.create_isolated_worktree(checkout, sha, worktrees_root=tmp_path / "wtroot2")
    assert good.ok is True and good.isolated is True
    assert good.path is not None and good.path.exists()
    assert good.path.name in _worktree_list(checkout)
    assert _git(good.path, "rev-parse", "HEAD").stdout.strip() == sha
    symref = _git(good.path, "symbolic-ref", "-q", "HEAD", check=False)
    assert symref.returncode != 0  # detached: no symbolic HEAD → no branch to delete


# ===== #58 defect #5 =====
@pytest.mark.xfail(strict=True, reason="PENDING (#58)")
def test_byte_isolation_proof_holds_on_linked_worktree_checkout(tmp_path):
    """When the checkout handed to create_isolated_worktree is itself a linked worktree (its ``.git``
    is a FILE pointing at a gitdir, not a directory), an index mutation during creation must still be
    caught as unprovable isolation. The byte-identical proof must not go blind just because ``.git``
    is a gitfile.

    technical (contract): build a primary checkout, then a DETACHED linked worktree of it whose
    ``.git`` is a file (precondition: ``(linked / '.git').is_file()``). Pass that linked worktree as
    the ``checkout`` with an injected ``add`` that stages a file into ITS index.
    create_isolated_worktree -> ok=False, isolated=False, non-empty reason. Today ``_snapshot`` reads
    ``<checkout>/.git/HEAD`` and ``<checkout>/.git/index`` as files under a ``.git`` DIRECTORY; on a
    linked worktree neither path exists, so both snapshots are b"" before AND after, the mutation is
    invisible, and isolation is falsely proven (ok=True/isolated=True). A correct impl resolves the
    real paths via ``git rev-parse --git-path HEAD`` / ``--git-path index`` (every read succeeding)
    and detects the index change.
    """
    from issueforge import workspace

    _origin, primary = _bare_origin_with_checkout(tmp_path)
    sha = _git(primary, "rev-parse", "HEAD").stdout.strip()

    # The checkout we hand in is ITSELF a linked worktree: its .git is a gitfile, not a directory.
    linked = tmp_path / "linked-checkout"
    _git(primary, "worktree", "add", "--detach", str(linked), sha)
    assert (linked / ".git").is_file()  # precondition: a linked-worktree .git is a FILE

    def _index_mutating_add(checkout_dir, worktree_path, target_sha):
        # Stage a file into the linked checkout's OWN index (its real index lives in the gitdir the
        # gitfile points at, not at <checkout>/.git/index).
        (Path(checkout_dir) / "sneaked.txt").write_text("x\n")
        _git(Path(checkout_dir), "add", "sneaked.txt")

    result = workspace.create_isolated_worktree(
        linked, sha, worktrees_root=tmp_path / "wtroot", add=_index_mutating_add
    )
    assert result.ok is False and result.isolated is False and result.reason


# ===== #58 defect #6 =====
@pytest.mark.xfail(strict=True, reason="PENDING (#58)")
def test_ambient_git_env_vars_cannot_redirect_operations(tmp_path):
    """Ambient GIT_* environment variables cannot redirect a workspace git operation away from the
    requested checkout. With GIT_WORK_TREE / GIT_INDEX_FILE (the GIT_DIR family) set in the
    environment to point elsewhere, ``reset_worktree`` still resets the requested worktree's own
    working tree and index — the malicious index/work-tree override does not let the mutation escape
    to a decoy tree, leaving the real worktree silently dirty.

    technical (contract): a worktree detached at ``sha`` with README committed as 'seed\\n' and a
    working-tree edit to 'mid-attempt edit\\n'. With ambient GIT_WORK_TREE=<decoy_tree> and
    GIT_INDEX_FILE=<decoy.index> set, reset_worktree(worktree, sha) -> the worktree's README is back
    to 'seed\\n', `git rev-parse HEAD` == sha, and `git status --porcelain` is empty (index + work
    tree both targeted the requested worktree). A non-scrubbing impl leaves README at
    'mid-attempt edit\\n' with status ' M README.md' because the reset materialized into the decoy
    GIT_WORK_TREE / GIT_INDEX_FILE.
    """
    from issueforge import workspace

    _origin, checkout = _bare_origin_with_checkout(tmp_path)
    sha = _git(checkout, "rev-parse", "HEAD").stdout.strip()
    worktree = workspace.create_isolated_worktree(checkout, sha).path
    assert (worktree / "README.md").read_text() == "seed\n"
    (worktree / "README.md").write_text("mid-attempt edit\n")

    # A hostile/misconfigured environment points git at a decoy work tree and index. A correct impl
    # scrubs the GIT_* family off its own subprocess env so the reset still targets `worktree`.
    decoy_tree = tmp_path / "decoy_tree"
    decoy_tree.mkdir()
    decoy_index = tmp_path / "decoy.index"
    hostile = {"GIT_WORK_TREE": str(decoy_tree), "GIT_INDEX_FILE": str(decoy_index)}
    saved = {k: os.environ.get(k) for k in hostile}
    os.environ.update(hostile)
    try:
        workspace.reset_worktree(worktree, sha)
    finally:
        for key, value in saved.items():
            if value is None:
                os.environ.pop(key, None)
            else:
                os.environ[key] = value

    assert (worktree / "README.md").read_text() == "seed\n"
    assert _git(worktree, "rev-parse", "HEAD").stdout.strip() == sha
    assert _git(worktree, "status", "--porcelain").stdout.strip() == ""


# ===== #58 defect #13 =====
@pytest.mark.xfail(strict=True, reason="PENDING (#58)")
def test_reset_worktree_refuses_non_isolated_path(tmp_path):
    """reset_worktree may destroy tracked work only inside a PROVEN-isolated linked worktree.
    Pointed at a dirty NORMAL checkout it must refuse before any reset --hard, so the developer's
    uncommitted edit survives; a real isolated worktree still resets. The two are distinguished by
    worktree registration (git-common-dir vs git-dir), not by trusting whatever path it is handed.

    technical (contract): clone a normal checkout (README committed as 'seed\\n'); write an
    uncommitted tracked edit 'precious edit on a normal checkout\\n'. reset_worktree(checkout,
    <HEAD commit sha>) RAISES; the checkout's HEAD is unchanged, the edit is byte-preserved, and
    `git status --porcelain` still reports it dirty (no reset --hard ran on the normal checkout).
    Then create_isolated_worktree(checkout, sha) -> a linked worktree; dirty it to 'mid-attempt
    edit\\n'; reset_worktree(worktree, sha) SUCCEEDS: HEAD == sha and README is back to committed
    'seed\\n' (the proven-isolated path still resets, so the fix distinguishes rather than refusing all).
    """
    from issueforge import workspace

    _origin, checkout = _bare_origin_with_checkout(tmp_path)
    sha = _git(checkout, "rev-parse", "HEAD").stdout.strip()

    # Leg 1: a dirty NORMAL checkout (not a linked worktree) must be REFUSED before any reset --hard.
    (checkout / "README.md").write_text("precious edit on a normal checkout\n")
    head_before = _git(checkout, "rev-parse", "HEAD").stdout.strip()
    with pytest.raises(Exception):
        workspace.reset_worktree(checkout, sha)
    assert _git(checkout, "rev-parse", "HEAD").stdout.strip() == head_before
    assert (checkout / "README.md").read_text() == "precious edit on a normal checkout\n"
    assert _git(checkout, "status", "--porcelain").stdout.strip() != ""

    # Leg 2: a proven-isolated worktree still resets (distinguish, do not refuse everything).
    worktree = workspace.create_isolated_worktree(checkout, sha).path
    (worktree / "README.md").write_text("mid-attempt edit\n")
    workspace.reset_worktree(worktree, sha)
    assert _git(worktree, "rev-parse", "HEAD").stdout.strip() == sha
    assert (worktree / "README.md").read_text() == "seed\n"


# ===== #58 defect #14 =====
@pytest.mark.xfail(strict=True, reason="PENDING (#58)")
def test_reset_worktree_requires_successful_reset_and_removes_untracked(tmp_path):
    """After reset_worktree, a worktree left contaminated by a prior attempt is fully clean: an
    untracked file that existed before is GONE (clean -fd ran), a dirty tracked edit is reverted,
    and HEAD sits at base_sha with a clean status. A reset that merely reverts tracked files but
    leaves untracked contamination is not enough; the next attempt would inherit stale files.

    technical (contract): a worktree with README committed as 'seed\\n'; before reset it carries an
    untracked 'leftover_untracked.txt' AND a dirty tracked edit ('mid-attempt edit\\n' in README).
    reset_worktree(worktree, <base_commit_sha>) -> 'leftover_untracked.txt' no longer exists,
    README is back to 'seed\\n', `git rev-parse HEAD` == base_sha, and `git status --porcelain` is
    empty (clean -fd removed the untracked file; a reset --hard alone leaves it behind).
    """
    from issueforge import workspace

    _origin, checkout = _bare_origin_with_checkout(tmp_path)
    sha = _git(checkout, "rev-parse", "HEAD").stdout.strip()
    worktree = workspace.create_isolated_worktree(checkout, sha).path

    # Contamination from a prior attempt: an untracked file plus a dirty tracked edit.
    (worktree / "leftover_untracked.txt").write_text("contaminant from a prior attempt\n")
    (worktree / "README.md").write_text("mid-attempt edit\n")

    workspace.reset_worktree(worktree, sha)

    # clean -fd must have removed the untracked contaminant; tracked edit reverts to committed.
    assert not (worktree / "leftover_untracked.txt").exists()
    assert (worktree / "README.md").read_text() == "seed\n"
    assert _git(worktree, "rev-parse", "HEAD").stdout.strip() == sha
    assert _git(worktree, "status", "--porcelain").stdout.strip() == ""

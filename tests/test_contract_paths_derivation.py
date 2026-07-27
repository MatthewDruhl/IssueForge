"""Committed PENDING acceptance suite for #143: a real run gets a real ``contract_paths``.

Verified current behavior (not the "unearned green" the first amendment described): with
``contract_paths=[]`` the authoring prompt at ``engine.py:851`` renders the degenerate line
``Author the failing tests only in these paths: .`` — it names NOWHERE. ``engine.py:983`` then runs
``git add --`` with no pathspec and stages nothing, the approval gate at ``engine.py:986`` is shown
an EMPTY diff, and ``engine.py:991`` calls ``git commit`` on an empty index. ``_candidate_git``
(``engine.py:775``) defaults to ``check=True``, so that commit RAISES ``CalledProcessError``: the
run dies loudly, and NO contract commit exists. This suite pins the two-part fix:

1. **The prompt must say where to author.** When the declared ``contract_paths`` is empty, the
   authoring instruction must name a concrete authoring location (the repo's test directory, per
   its adapter/config convention) instead of an empty path list. Without this the derivation below
   is inert: a provider that OBEYS the prompt writes nothing and every real run pauses.
2. **The engine must derive the contract from the worktree.** Between authoring and staging, the
   engine derives the contract paths from the candidate worktree's own ADDED files (vs
   ``base_sha``) and stages + commits exactly THOSE as the frozen contract, with that diff shown at
   the approval gate. When the derived set is empty the run PAUSES with ``no_contract_paths``
   before the gate — no approver call, no commit, and an untouched index.

How the fixture stays honest (the #115 failure shape: a fixture that encodes a more capable author
than reality):

- **The fake author OBEYS the prompt.** ``_Fakes`` parses the authoring prompt for the location(s)
  it was told to author in and writes ONLY there. Handed today's prompt, which names nowhere, it
  writes NOTHING — exactly what a compliant provider does. Its obedience model
  (``_prompt_locations``): on a line that carries an authoring verb AND the word "test", take the
  payload after ``:`` (the whole line when there is no colon) and accept a token ending in ``.py``
  or containing ``/``; a bare directory name is accepted only from a colon payload, so prose like
  "You are authoring pytest acceptance tests" names nothing. ``.py`` tokens win over directories.
  The issue body is removed before scanning, so the provider follows the ENGINE's directive rather
  than a path the issue text merely mentions.
- **Unguessable, un-globbable, multi-file authored set.** Two files per run under randomly-named
  paths, one of which does NOT match ``test_*.py``, so an implementation that globs a plausible
  test pattern and takes the first hit cannot reproduce the set.
- **ADDED is distinguished from DIRTY.** The base commit carries a pre-existing test file, a file
  the author MODIFIES, and a file the author DELETES. Only the added files may be in the contract,
  so a dirty-scan or a test-file-scan implementation fails — including in the pause case, where the
  author dirties the tree while adding nothing.
- **The commit's real content is asserted**, byte-for-byte, from the commit object.

Dual-layer docstrings: a plain-English behavior summary, then the ``technical (contract):`` golden
values.
"""

from __future__ import annotations

import posixpath
import re
import subprocess
import uuid
from pathlib import Path, PurePosixPath
from types import SimpleNamespace

import pytest

REPO_ALIAS = "DandD"
WRITE_SCOPE = ["src/dandd/greet.py"]
ACCEPTANCE_COMMAND = ["pytest", "-k", "derivedcase"]
BASELINE_COMMAND = ["pytest", "-p", "no:cacheprovider"]

PAUSE_REASON_NO_PATHS = "no_contract_paths"
PAUSE_REASON_REJECTED = "rejected_by_approver"

# Tracked files committed at base so the fixture can tell ADDED from merely DIRTY.
MODIFIED_DISTRACTOR = "README.md"
DELETED_DISTRACTOR = "docs/legacy_notes.txt"

# The repo's test-directory convention. ``.issueforge.toml`` carries only ``baseline`` and
# ``framework``, so no configured key exposes a test directory at the authoring point; the
# convention is encoded in the codebase itself (``audit._test_path`` accepts ``tests/test_*.py``).
# Note ``docs/`` also exists in the seeded worktree, so "names an existing directory" is not enough.
TEST_DIR = "tests"


# --------------------------------------------------------------------------- git helpers


def _run_git(repo: Path, *args: str) -> str:
    out = subprocess.run(
        ["git", "-C", str(repo), *args], capture_output=True, text=True, check=True
    )
    return out.stdout


def _head(repo: Path) -> str:
    return _run_git(repo, "rev-parse", "HEAD").strip()


def _all_commit_shas(repo: Path) -> set[str]:
    """Every commit reachable from any ref OR the reflog — so a commit-then-reset leaves a trace."""
    return set(_run_git(repo, "rev-list", "--all", "--reflog").split())


def _commit_files(repo: Path, sha: str) -> list[str]:
    """The paths commit ``sha`` actually changed (empty for an empty-tree commit)."""
    out = _run_git(repo, "show", "--name-only", "--format=", sha)
    return sorted(line.strip() for line in out.splitlines() if line.strip())


def _blob_at(repo: Path, sha: str, path: str) -> str:
    """The EXACT bytes of ``path`` in commit ``sha`` (no stripping — content equality is asserted)."""
    return _run_git(repo, "cat-file", "-p", f"{sha}:{path}")


def _staged_paths(repo: Path) -> list[str]:
    out = _run_git(repo, "diff", "--cached", "--name-only")
    return sorted(line.strip() for line in out.splitlines() if line.strip())


# --------------------------------------------------------------------------- prompt obedience


_AUTHOR_VERB = re.compile(r"\b(author|creat|place|put|writ)\w*", re.I)
_TEST_WORD = re.compile(r"\btests?\b", re.I)
_TRIM = ".,;:'\"`()[]<>"


def _prompt_locations(prompt: str, issue_body: str, worktree: Path) -> list[str]:
    """Where this prompt TELLS a provider to author (see the module docstring's obedience model).

    Returns [] when the prompt names nowhere — which is what today's empty-``contract_paths``
    rendering does, and why an obedient provider writes nothing on a real run.
    """
    scanned = prompt.replace(issue_body, " ") if issue_body else prompt
    files: list[str] = []
    dirs: list[str] = []
    for line in scanned.splitlines():
        if not (_AUTHOR_VERB.search(line) and _TEST_WORD.search(line)):
            continue
        has_colon = ":" in line
        payload = line.split(":", 1)[1] if has_colon else line
        for raw in re.split(r"[\s,]+", payload):
            token = raw.strip(_TRIM)
            if not token:
                continue
            if token.endswith(".py"):
                files.append(token)
            elif "/" in token or (has_colon and (worktree / token).is_dir()):
                dirs.append(token.rstrip("/"))
    chosen = files or dirs
    return list(dict.fromkeys(chosen))


def _under_test_dir(location: str) -> bool:
    """True when ``location`` IS the repo's test directory or sits under it.

    ``..`` segments are normalised away FIRST. Checking only the leading component would
    accept ``tests/../docs`` and ``tests/../acceptance.py``, which escape the test directory
    while still starting with ``tests``.
    """
    normalised = posixpath.normpath(location.strip().strip("/"))
    if normalised in {"", ".", ".."}:
        return False
    parts = PurePosixPath(normalised).parts
    return bool(parts) and parts[0] == TEST_DIR and ".." not in parts


class _StopAfterAuthoring(Exception):
    """Raised by the fake author to end the run right after the authoring invocation, so a test
    about the PROMPT asserts on the prompt instead of on whatever happens downstream."""


# --------------------------------------------------------------------------- fake collaborators


def _green_evidence():
    from issueforge.adapters.base import BaselineStatus

    return SimpleNamespace(
        status=BaselineStatus.GREEN, collected=1, executed=1, nodes=(), exit_code=0
    )


def _accepted_proof(base_sha: str, node_id: str):
    record = SimpleNamespace(
        nodeid=node_id,
        exception_type="AssertionError",
        assertion_line=1,
        message="RED-EVIDENCE-MARKER",
    )
    return SimpleNamespace(
        accepted=True,
        reason="behavioral_red",
        records=(record,),
        base_sha=base_sha,
        added_ids=(node_id,),
        head_sha="",
    )


class _Fakes:
    """The candidate stage's external boundaries, faked as the #114 suite fakes them.

    ``mode="obedient"``: the author writes ONLY where the prompt tells it to (nothing when the
    prompt names nowhere). ``mode="silent"``: the author adds NOTHING at all — a provider that
    produced no files — while still dirtying the tree, so an implementation deriving from dirty
    files rather than ADDED files does not pause.
    """

    def __init__(
        self,
        worktree: Path,
        base_sha: str,
        sentinel,
        issue_body: str,
        *,
        mode: str = "obedient",
        approves: bool = True,
        stop_after_authoring: bool = False,
    ):
        self.worktree = worktree
        self.base_sha = base_sha
        self.sentinel = sentinel
        self.issue_body = issue_body
        self.mode = mode
        self.approves = approves
        self.stop_after_authoring = stop_after_authoring
        self.invoke_calls: list[dict] = []
        self.author_prompt: str | None = None
        self.written_paths: list[str] = []
        self.approver_reviews: list[object] = []
        self.baseline_calls: list[list[str]] = []

    # -- the authoring / implementation provider --

    def _author(self, cwd: Path, prompt: str) -> None:
        self.author_prompt = prompt
        # Dirty the tree WITHOUT adding anything: a tracked file modified and a tracked file
        # deleted. Neither may ever appear in the frozen contract.
        (cwd / MODIFIED_DISTRACTOR).write_text("touched by the author\n", encoding="utf-8")
        (cwd / DELETED_DISTRACTOR).unlink()
        if self.mode == "silent":
            return
        for location in _prompt_locations(prompt, self.issue_body, cwd):
            targets = (
                [location]
                if location.endswith(".py")
                else [f"{location}/{rel}" for rel in self.sentinel.relative_targets]
            )
            for rel in targets:
                target = cwd / rel
                target.parent.mkdir(parents=True, exist_ok=True)
                target.write_text(self.sentinel.blob_for(rel), encoding="utf-8")
                self.written_paths.append(rel)

    def invoke(
        self,
        profile,
        prompt,
        *,
        cwd,
        run_id=None,
        role="primary",
        timeout=None,
        secrets=frozenset(),
        session=None,
        **_kw,
    ):
        cwd = Path(cwd)
        is_author = len(self.invoke_calls) == 0
        self.invoke_calls.append(
            {"prompt": prompt, "cwd": cwd, "phase": "author" if is_author else "impl"}
        )
        if is_author:
            self._author(cwd, prompt)
            if self.stop_after_authoring:
                raise _StopAfterAuthoring(run_id)
        else:
            target = cwd / WRITE_SCOPE[0]
            target.parent.mkdir(parents=True, exist_ok=True)
            target.write_text("def greet(name):\n    return f'hi {name}'\n", encoding="utf-8")
        from issueforge.providers import InvocationStatus

        return SimpleNamespace(
            role=role,
            provider="fake",
            session_id=f"sess-{len(self.invoke_calls)}",
            prompt=prompt,
            stdout="",
            stderr="",
            returncode=0,
            duration_ms=1.0,
            status=InvocationStatus.OK,
            timed_out=False,
            artifact_path=self.worktree / "transcript.txt",
        )

    def prove_red(
        self,
        run_id,
        *,
        targeted_ids,
        base_checkout,
        candidate_worktree,
        base_sha,
        adapter,
        provisioner=None,
        store=None,
        secrets=frozenset(),
    ):
        return _accepted_proof(self.base_sha, self.sentinel.node_id)

    def approver(self, review):
        self.approver_reviews.append(review)
        return self.approves

    def run_baseline(self, worktree, base_command, *, adapter, provisioner=None, timeout=None):
        self.baseline_calls.append(list(base_command))
        return _green_evidence()


# --------------------------------------------------------------------------- shared setup


def _sentinel():
    """A per-run, UNGUESSABLE authored-file identity.

    ``relative_targets`` are TWO paths written under whatever location the prompt names: one
    ``test_*.py`` file and one helper in a randomly-named subdirectory that does NOT match a test
    glob, so an implementation that globs a plausible test pattern (or takes the first added file)
    cannot reproduce the complete set. ``seeded_rel`` is a PRE-EXISTING tracked test file such a
    glob would wrongly sweep in.
    """
    token = uuid.uuid4().hex[:10]
    other = uuid.uuid4().hex[:10]
    targets = (f"test_{token}.py", f"{other}/helper_{other}.py")
    return SimpleNamespace(
        token=token,
        relative_targets=targets,
        seeded_rel=f"tests/test_seeded_{other}.py",
        decoy_path=f"tests/test_decoy_{token}.py",
        marker=f"AUTHORED-MARKER-{token}",
        node_id=f"tests/{targets[0]}::test_case_{token}",
        blob_for=lambda rel, token=token: (
            f"# AUTHORED-MARKER-{token}\n# authored into {rel}\ndef test_case_{token}():\n    assert False\n"
        ),
    )


def _seed(make_git_repo, sentinel, *, declared_contract_paths):
    """Build a candidate worktree (carrying the tracked distractors and one pre-existing test) plus
    a SEPARATE registered normal checkout, register the checkout, and persist a run record whose
    issue body names the DECOY path and whose ``contract_paths`` is what the caller declares
    (``[]`` for a real run, per ``github.read_issue_body``)."""
    from issueforge import registry, store

    candidate = make_git_repo(name="candidate")
    base_checkout = make_git_repo(name="base-checkout")
    for repo in (candidate, base_checkout):
        _run_git(repo, "config", "user.email", "engine@issueforge.invalid")
        _run_git(repo, "config", "user.name", "IssueForge Engine")

    (candidate / MODIFIED_DISTRACTOR).write_text("original readme\n", encoding="utf-8")
    (candidate / DELETED_DISTRACTOR).parent.mkdir(parents=True, exist_ok=True)
    (candidate / DELETED_DISTRACTOR).write_text("legacy\n", encoding="utf-8")
    seeded = candidate / sentinel.seeded_rel
    seeded.parent.mkdir(parents=True, exist_ok=True)
    seeded.write_text(f"def test_seeded_{sentinel.token}():\n    assert True\n", encoding="utf-8")
    _run_git(candidate, "add", "-A")
    _run_git(candidate, "commit", "-qm", "seed distractors")
    base_sha = _head(candidate)
    registry.register(REPO_ALIAS, str(base_checkout))

    run_id = f"run-143-{sentinel.token}"
    issue_body = f"DandD#111 GREET-BEHAVIOR: greet(name) must return 'hi <name>'. Suggested tests: {sentinel.decoy_path}"
    fields = {
        "run_id": run_id,
        "status": "running",
        "repo": REPO_ALIAS,
        "issue": issue_body,
        "candidate_worktree": str(candidate),
        "base_sha": base_sha,
        "write_scope": WRITE_SCOPE,
        "contract_paths": list(declared_contract_paths),
        "acceptance_command": ACCEPTANCE_COMMAND,
        "baseline_command": BASELINE_COMMAND,
    }
    store.RunStore().apply(run_id, lambda _r: fields, create=True)
    return run_id, store.RunStore().read(run_id), candidate, base_sha, issue_body


def _call(record, fakes):
    from issueforge import engine

    return engine.run_candidate(
        record,
        profile=SimpleNamespace(name="fake"),
        approver=fakes.approver,
        invoke=fakes.invoke,
        prove_red=fakes.prove_red,
        run_baseline=fakes.run_baseline,
    )


def _call_checked(record, fakes, claim: str):
    """Run the stage, reporting a git failure raised INSIDE ``run_candidate`` as an assertion about
    THIS test's ``claim``. Today the no-pathspec path dies at ``git commit`` on an empty index; a
    correct implementation never reaches this conversion."""
    try:
        return _call(record, fakes)
    except subprocess.CalledProcessError as error:
        raise AssertionError(
            f"{claim}; instead run_candidate failed: {' '.join(str(a) for a in error.cmd)} -> exit {error.returncode}"
        ) from None


# =========================================================================== the suite


def test_test_dir_containment_rejects_paths_that_escape_it() -> None:
    """GREEN guard: the containment check used by the prompt test cannot be fooled by ``..``.

    The prompt criterion below is only as strong as ``_under_test_dir``. Checking just the
    leading path component would accept ``tests/../docs``, which starts with ``tests`` but
    lands outside it -- so an implementation could direct authors anywhere and still pass.
    This pins the normalisation so that hole cannot silently reopen.
    """
    for accepted in ("tests", "tests/", "/tests", "tests/unit", "tests/test_x.py"):
        assert _under_test_dir(accepted), accepted

    for rejected in (
        "docs",
        "acceptance.py",
        "tests/../docs",
        "tests/../acceptance.py",
        "tests/unit/../../docs",
        "../tests",
        "tests/..",
        "",
        ".",
    ):
        assert not _under_test_dir(rejected), rejected


def test_authoring_prompt_names_where_to_author_when_no_paths_are_declared(
    make_git_repo, isolated_state_home
):
    """When the issue declares no test paths, the instruction handed to the test-writing AI still
    tells it WHERE to put the tests, instead of handing it an empty list of places and expecting
    files anyway.

    technical (contract): with record["contract_paths"] == [], the FIRST invoke's prompt (the REAL
    _authoring_prompt output) names at least one authoring location — a token ending in .py, or
    containing "/", or a colon-payload token naming an existing directory, on a line carrying an
    authoring verb and the word "test" — EVERY named location is TEST_DIR itself or sits under it,
    and TEST_DIR is a real directory of the candidate worktree. Naming docs/ (which exists in this
    worktree) or a root-level acceptance.py is NOT enough. Today the line renders as
    "...in these paths: ." and names nothing.
    """
    sentinel = _sentinel()
    _run_id, record, candidate, base_sha, issue_body = _seed(
        make_git_repo, sentinel, declared_contract_paths=[]
    )
    fakes = _Fakes(candidate, base_sha, sentinel, issue_body, stop_after_authoring=True)

    with pytest.raises(_StopAfterAuthoring):
        _call(record, fakes)

    prompt = fakes.author_prompt
    locations = _prompt_locations(prompt, issue_body, candidate)
    assert locations, (
        "the authoring prompt names no place to author when contract_paths is empty, so a provider "
        f"that obeys it writes nothing; prompt was:\n{prompt}"
    )
    assert (candidate / TEST_DIR).is_dir(), "fixture precondition: the test directory must exist"
    off_convention = [loc for loc in locations if not _under_test_dir(loc)]
    assert not off_convention, (
        f"the authoring prompt points at {off_convention}, outside the repo's test directory "
        f"{TEST_DIR!r}; authored tests must land in the test directory, not somewhere like docs/ "
        "or a root-level acceptance.py"
    )


def test_contract_commit_freezes_exactly_the_files_authoring_added(
    make_git_repo, isolated_state_home
):
    """With no declared paths, the run freezes exactly the files the test-writing step actually
    created — all of them, byte for byte — and nothing else: not the file it edited, not the file
    it deleted, not tests that were already in the repo.

    technical (contract): the contract commit's changed-file list EQUALS the sorted set of paths
    the obedient author wrote (two files, one of which does not match test_*.py); each path's blob
    in that commit EQUALS the authored bytes exactly; MODIFIED_DISTRACTOR, DELETED_DISTRACTOR and
    the pre-existing seeded test are absent; the commit's parent is base_sha.
    """
    sentinel = _sentinel()
    _run_id, record, candidate, base_sha, issue_body = _seed(
        make_git_repo, sentinel, declared_contract_paths=[]
    )
    fakes = _Fakes(candidate, base_sha, sentinel, issue_body)

    result = _call_checked(
        record, fakes, "the contract commit must freeze exactly the files authoring added"
    )

    assert result.paused is False, f"run paused: {result.pause_reason}"
    assert len(fakes.written_paths) == 2, (
        "the obedient author wrote no files, so the prompt named nowhere to author"
    )
    assert _commit_files(candidate, result.contract_commit) == sorted(fakes.written_paths)
    for rel in fakes.written_paths:
        assert _blob_at(candidate, result.contract_commit, rel) == sentinel.blob_for(rel)
    committed = _commit_files(candidate, result.contract_commit)
    for excluded in (MODIFIED_DISTRACTOR, DELETED_DISTRACTOR, sentinel.seeded_rel):
        assert excluded not in committed, f"{excluded} is not an ADDED file and must not be frozen"
    assert _run_git(candidate, "rev-parse", f"{result.contract_commit}^").strip() == base_sha


def test_approval_gate_shows_the_real_authored_test_diff(make_git_repo, isolated_state_home):
    """The human approving the contract sees a real diff adding the authored test files, instead of
    the blank diff an undeclared-paths run shows today.

    technical (contract): the approver is called exactly once and its review.diff is a unified diff
    carrying, for EVERY authored path, the "diff --git a/<p> b/<p>", "new file mode",
    "--- /dev/null" and "+++ b/<p>" headers plus each authored line as a "+" line; it does NOT
    mention the modified, deleted, or pre-existing files. The approver rejects, so the run pauses
    at the gate with "rejected_by_approver".
    """
    sentinel = _sentinel()
    _run_id, record, candidate, base_sha, issue_body = _seed(
        make_git_repo, sentinel, declared_contract_paths=[]
    )
    fakes = _Fakes(candidate, base_sha, sentinel, issue_body, approves=False)

    result = _call(record, fakes)

    assert len(fakes.approver_reviews) == 1
    diff = fakes.approver_reviews[0].diff
    assert fakes.written_paths, "the authoring step wrote nothing, so there is no diff to review"
    for rel in fakes.written_paths:
        assert f"diff --git a/{rel} b/{rel}" in diff, (
            f"no unified-diff header for {rel} in:\n{diff}"
        )
        assert "new file mode" in diff
        assert "--- /dev/null" in diff
        assert f"+++ b/{rel}" in diff
        for line in sentinel.blob_for(rel).splitlines():
            assert f"+{line}" in diff, f"authored line {line!r} missing from the reviewed diff"
    for excluded in (MODIFIED_DISTRACTOR, DELETED_DISTRACTOR, sentinel.seeded_rel):
        assert excluded not in diff, f"the reviewed diff must be the contract only, not {excluded}"
    assert result.pause_reason == PAUSE_REASON_REJECTED


def test_derived_paths_are_the_contract_downstream(make_git_repo, isolated_state_home):
    """The derived contract is the run's contract everywhere after the gate: the implementation
    step is handed the frozen tests' exact content, and the saved run lists exactly those paths.

    technical (contract): the SECOND invoke's prompt contains the EXACT authored bytes of every
    derived path and does NOT contain the pre-existing seeded test's content; the persisted
    record's "contract_paths" EQUALS the sorted set of authored paths (no extras).
    """
    from issueforge import store

    sentinel = _sentinel()
    run_id, record, candidate, base_sha, issue_body = _seed(
        make_git_repo, sentinel, declared_contract_paths=[]
    )
    fakes = _Fakes(candidate, base_sha, sentinel, issue_body)

    _call_checked(
        record, fakes, "the derived contract must reach the implementation prompt and the record"
    )

    impl_calls = [call for call in fakes.invoke_calls if call["phase"] == "impl"]
    assert len(impl_calls) == 1
    prompt = impl_calls[0]["prompt"]
    for rel in fakes.written_paths:
        assert sentinel.blob_for(rel) in prompt, (
            f"frozen contract for {rel} missing from the prompt"
        )
    assert f"test_seeded_{sentinel.token}" not in prompt
    saved = store.RunStore().read(run_id)
    assert sorted(saved["contract_paths"]) == sorted(fakes.written_paths)


def test_authoring_adding_nothing_pauses_with_no_gate_and_no_commit(
    make_git_repo, isolated_state_home
):
    """If the test-writing step adds no files at all — even though it changed and removed other
    files — the run stops with a named reason instead of asking a human to approve an empty change,
    and it leaves nothing staged and nothing committed.

    technical (contract): with a silent author the result is paused True, pause_reason
    "no_contract_paths", contract_commit None; the durable run state is status "paused" with no
    stored contract_commit; the approver was NEVER called (it would have REJECTED, so a run that
    consulted it pauses with a different reason); no implementation invoke happened; candidate HEAD
    is still base_sha; the commit set (refs + reflog) is unchanged; and the index is empty, so
    nothing was staged before the pause.
    """
    from issueforge import store

    sentinel = _sentinel()
    run_id, record, candidate, base_sha, issue_body = _seed(
        make_git_repo, sentinel, declared_contract_paths=[]
    )
    commits_before = _all_commit_shas(candidate)
    fakes = _Fakes(candidate, base_sha, sentinel, issue_body, mode="silent", approves=False)

    result = _call(record, fakes)

    assert result.pause_reason == PAUSE_REASON_NO_PATHS
    assert result.paused is True
    assert result.contract_commit is None
    assert fakes.approver_reviews == [], "the approval gate was reached on an empty contract"
    assert [call["phase"] for call in fakes.invoke_calls] == ["author"]
    saved = store.RunStore().read(run_id)
    assert saved["status"] == "paused"
    assert saved.get("contract_commit") is None
    assert _head(candidate) == base_sha
    assert _all_commit_shas(candidate) == commits_before
    assert _staged_paths(candidate) == [], "nothing may be staged when the derived set is empty"


def test_declared_contract_paths_still_reach_the_frozen_contract(
    make_git_repo, isolated_state_home
):
    """A run whose issue DOES declare its test path keeps working exactly as before: the declared
    file the author wrote is the frozen contract, and the files it merely edited or deleted are not.

    technical (contract): with record["contract_paths"] == ["tests/<authored>.py"], the obedient
    author writes that one declared file; the run is not paused and the contract commit's
    changed-file list EQUALS [that path] with the authored bytes exactly. GREEN today (regression
    guard): the declared path is staged today, and after the fix the derived ADDED set is that same
    single file.
    """
    sentinel = _sentinel()
    declared = f"tests/{sentinel.relative_targets[0]}"
    _run_id, record, candidate, base_sha, issue_body = _seed(
        make_git_repo, sentinel, declared_contract_paths=[declared]
    )
    fakes = _Fakes(candidate, base_sha, sentinel, issue_body)

    result = _call(record, fakes)

    assert result.paused is False, f"run paused: {result.pause_reason}"
    assert fakes.written_paths == [declared]
    assert _commit_files(candidate, result.contract_commit) == [declared]
    assert _blob_at(candidate, result.contract_commit, declared) == sentinel.blob_for(declared)

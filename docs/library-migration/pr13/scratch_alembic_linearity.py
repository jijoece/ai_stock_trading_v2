"""PR 13 scratch reproduction (evaluation only, not merged into src/).

Empirically tests `MASTER_PLAN.md` row 13 question (b): "whether Alembic's
branching revision graph can be constrained to linear-only history matching
the current monotonic gate" — this repository's actual migration mechanism
today, `storage/schema_version.py`'s `_MIGRATIONS: dict[int, ...]` ledger: a
single strictly-increasing integer, applied in ascending order, with no
concept of a branch or a merge.

Builds a real, disposable Alembic environment (a temp directory, a real
`alembic.ini`-equivalent `Config`, real revision script files written to
disk) against a throwaway SQLite database, using the actual `alembic`
package APIs (`alembic.command`, `alembic.script.ScriptDirectory`) — not a
description of how Alembic behaves.

Run: /tmp/pr13_scratch_venv/bin/python scratch_alembic_linearity.py
"""
from __future__ import annotations

import shutil
import tempfile
from pathlib import Path

from alembic import command
from alembic.config import Config
from alembic.script import ScriptDirectory
from alembic.util.exc import CommandError

SCRIPT_PY_MAKO = """\
\"\"\"${message}\"\"\"
revision = ${repr(up_revision)}
down_revision = ${repr(down_revision)}
depends_on = ${repr(depends_on)}

def upgrade():
    pass

def downgrade():
    pass
"""


def _log(title: str) -> None:
    print(f"\n=== {title} ===")


def build_env(root: Path) -> Config:
    versions_dir = root / "versions"
    versions_dir.mkdir(parents=True)
    (root / "script.py.mako").write_text(SCRIPT_PY_MAKO)
    db_path = root / "scratch.db"
    cfg = Config()
    cfg.set_main_option("script_location", str(root))
    cfg.set_main_option("sqlalchemy.url", f"sqlite:///{db_path}")
    cfg.set_main_option("version_locations", str(versions_dir))
    # alembic.command.init normally writes env.py too; ScriptDirectory only
    # strictly needs script.py.mako + versions/ for the graph operations
    # this scratch test exercises (revision creation, get_heads(), upgrade).
    env_py = root / "env.py"
    env_py.write_text(
        "from alembic import context\n"
        "def run_migrations_offline():\n"
        "    context.configure(url=context.config.get_main_option('sqlalchemy.url'))\n"
        "    with context.begin_transaction():\n"
        "        context.run_migrations()\n"
        "def run_migrations_online():\n"
        "    from sqlalchemy import engine_from_config, pool\n"
        "    connectable = engine_from_config(context.config.get_section(context.config.config_ini_section) or {}, prefix='sqlalchemy.', poolclass=pool.NullPool)\n"
        "    with connectable.connect() as connection:\n"
        "        context.configure(connection=connection)\n"
        "        with context.begin_transaction():\n"
        "            context.run_migrations()\n"
        "if context.is_offline_mode():\n"
        "    run_migrations_offline()\n"
        "else:\n"
        "    run_migrations_online()\n"
    )
    return cfg


def case_1_linear_chain_one_head(cfg: Config) -> str:
    _log("Case 1: build a linear 3-revision chain (control) — exactly one head")
    r1 = command.revision(cfg, message="baseline", rev_id="0001")
    r2 = command.revision(cfg, message="add column", rev_id="0002", head="0001")
    r3 = command.revision(cfg, message="add index", rev_id="0003", head="0002")
    script = ScriptDirectory.from_config(cfg)
    heads = script.get_heads()
    print(f"    revisions created: {r1.revision}, {r2.revision}, {r3.revision}")
    print(f"    heads: {heads}")
    assert heads == ["0003"], f"expected exactly one head '0003', got {heads}"
    print("PASS: linear chain has exactly one head")
    return "0003"


def case_2_branch_creates_two_heads(cfg: Config, parent: str) -> None:
    _log("Case 2: two revisions off the SAME parent (a real branch)")
    command.revision(cfg, message="branch A", rev_id="0004a", head=parent)
    # Alembic's own default already refuses this: `revision(head=parent)` a
    # second time raises "Revision 0003 is not a head revision; please
    # specify --splice to create a new branch from this revision" — Alembic
    # itself requires an explicit, named override to branch at all. This is
    # a real built-in guard, not something this evaluation invented; the
    # test exercises the override to reach a branched state to evaluate the
    # remaining cases against, not because it is the easy/default path.
    try:
        command.revision(cfg, message="branch B (no splice)", rev_id="0004brejected", head=parent)
        print("FAIL: Alembic allowed a second child of an already-referenced head "
              "without --splice")
    except CommandError as exc:
        print(f"PASS: Alembic's own default refuses an un-spliced branch — {exc}")
    command.revision(cfg, message="branch B", rev_id="0004b", head=parent, splice=True)
    script = ScriptDirectory.from_config(cfg)
    heads = sorted(script.get_heads())
    print(f"    heads after branching off {parent!r} with splice=True: {heads}")
    if len(heads) == 2:
        print("PASS (demonstrates the residual risk): `--splice`/`splice=True` still lets a "
              "branch through when a developer deliberately (or mistakenly) reaches for it — "
              "the default resists an accidental branch but does not make branching impossible")
    else:
        print(f"UNEXPECTED: expected 2 heads, got {len(heads)}")


def case_2b_concurrent_checkouts_bypass_unspliced_guard(root: Path, parent: str) -> None:
    _log(
        "Case 2b: two independent checkouts each branch off the SAME parent "
        "without ever seeing the other's new revision file — does Case 2's "
        "un-spliced-branch refusal also catch this, or only a branch that is "
        "already visible within one script directory when the guarded "
        "command runs?"
    )
    # Two independent checkouts of the script-directory state as it existed
    # right after Case 1 (only 0001-0003 exist, 0003 has no child yet) — each
    # stands in for a developer who has pulled the repository at that point
    # and has not seen the other's not-yet-shared revision file, exactly as
    # two developers working from separate git checkouts would.
    checkout_a = Path(tempfile.mkdtemp(prefix="pr13_alembic_checkout_a_"))
    checkout_b = Path(tempfile.mkdtemp(prefix="pr13_alembic_checkout_b_"))
    combined = Path(tempfile.mkdtemp(prefix="pr13_alembic_combined_"))
    try:
        for checkout in (checkout_a, checkout_b):
            shutil.rmtree(checkout)
            shutil.copytree(root, checkout)

        def _cfg_for(checkout: Path) -> Config:
            cfg = Config()
            cfg.set_main_option("script_location", str(checkout))
            cfg.set_main_option("sqlalchemy.url", f"sqlite:///{checkout / 'scratch.db'}")
            cfg.set_main_option("version_locations", str(checkout / "versions"))
            return cfg

        # Developer A creates a revision off `parent` in checkout A — no
        # --splice needed: checkout A's own versions/ directory has no child
        # of `parent` yet.
        command.revision(_cfg_for(checkout_a), message="concurrent A", rev_id="0004concurrentA", head=parent)
        # Developer B, independently and without having pulled A's new file,
        # does the same in checkout B — also succeeds without --splice, for
        # the same reason: from checkout B's own view, `parent` is still an
        # unreferenced head.
        command.revision(_cfg_for(checkout_b), message="concurrent B", rev_id="0004concurrentB", head=parent)
        print(
            "    neither developer's local `alembic revision` call raised "
            "CommandError or needed --splice"
        )

        # Simulate combining both checkouts' file changes (e.g. a `git
        # merge`/`git pull` that brings both new revision files into one
        # versions/ directory) — only now, after the fact, does the branch
        # become visible in a single script directory.
        shutil.rmtree(combined)
        shutil.copytree(checkout_a, combined)
        for f in (checkout_b / "versions").glob("*concurrentB*"):
            shutil.copy(f, combined / "versions" / f.name)
        script = ScriptDirectory.from_config(_cfg_for(combined))
        heads = sorted(script.get_heads())
        print(f"    heads after combining both checkouts' revision files: {heads}")
        if len(heads) == 2:
            print(
                "PASS (demonstrates the residual risk Case 2 does not cover): "
                "combining two independently created, un-spliced revisions off "
                "the same parent still produces a real branch — Case 2's "
                "un-spliced-branch refusal only fires when the second "
                "`alembic revision` call can already see the first developer's "
                "file on disk in the same script directory; it cannot see "
                "across two developers' separate checkouts, so the common "
                "concurrent-development branch is not caught by Alembic's "
                "default at revision-creation time at all"
            )
        else:
            print(f"UNEXPECTED: expected 2 heads, got {len(heads)}")
    finally:
        shutil.rmtree(checkout_a, ignore_errors=True)
        shutil.rmtree(checkout_b, ignore_errors=True)
        shutil.rmtree(combined, ignore_errors=True)


def case_3_upgrade_head_singular_fails_with_multiple_heads(cfg: Config) -> None:
    _log("Case 3: `alembic upgrade head` (singular target) with multiple heads present")
    try:
        command.upgrade(cfg, "head")
        print("FAIL: upgrade to singular 'head' succeeded despite multiple heads existing")
    except CommandError as exc:
        print(f"PASS: Alembic itself refuses an ambiguous singular-head upgrade — {exc}")


def _down_revision_is_merge(down_revision) -> bool:
    # Alembic represents a merge revision's down_revision as a tuple of
    # parent revision ids; an ordinary linear revision's down_revision is a
    # single string (or None for a root).
    return isinstance(down_revision, tuple)


def linear_only_gate(script: ScriptDirectory) -> list[str]:
    """The exact check a CI job could run to enforce the repository's
    monotonic-ledger model onto Alembic's DAG. Returns a list of violation
    messages (empty means the graph is a single strict chain, isomorphic to
    schema_version.py's `_MIGRATIONS` dict: one predecessor per revision,
    one head, no merges, no branches, no `depends_on` cross-edges).

    `depends_on` is a separate dependency mechanism from `down_revision`:
    Alembic does not count it toward `get_heads()` or toward a revision's
    down-revision children, so a revision can carry a `depends_on` edge
    while the graph still reports exactly one head and no down_revision
    branch — case 7/8 below reproduce this. Any non-empty `dependencies`
    is therefore rejected outright rather than incorporated into the
    linearity proof: schema_version.py's ledger has no dependency concept
    at all, so no `depends_on` edge is equivalent to it."""
    violations = []
    heads = script.get_heads()
    if len(heads) != 1:
        violations.append(f"expected exactly 1 head, found {len(heads)}: {sorted(heads)}")
    seen_as_down_revision: dict[str, list[str]] = {}
    for rev in script.walk_revisions():
        if _down_revision_is_merge(rev.down_revision):
            violations.append(
                f"revision {rev.revision!r} is a merge revision (down_revision="
                f"{rev.down_revision!r}) — schema_version.py's ledger has no merge concept"
            )
            continue
        if rev.dependencies:
            violations.append(
                f"revision {rev.revision!r} has a depends_on dependency edge "
                f"(dependencies={rev.dependencies!r}) outside down_revision — "
                "schema_version.py's ledger has no dependency concept"
            )
        if rev.down_revision is not None:
            seen_as_down_revision.setdefault(rev.down_revision, []).append(rev.revision)
    for parent, children in seen_as_down_revision.items():
        if len(children) > 1:
            violations.append(
                f"revision {parent!r} has {len(children)} children {sorted(children)} — "
                "a branch, not a linear chain"
            )
    return violations


def case_4_linear_only_gate_catches_the_branch(cfg: Config) -> None:
    _log("Case 4: a CI-style 'linear-only' gate function, run against the now-branched graph")
    script = ScriptDirectory.from_config(cfg)
    violations = linear_only_gate(script)
    print(f"    violations found: {len(violations)}")
    for v in violations:
        print(f"    - {v}")
    if violations:
        print("PASS: a straightforward custom gate (assert exactly one head + no revision "
              "has more than one child + no merge revisions) reliably detects the branch — "
              "Alembic exposes everything the gate needs via ScriptDirectory")
    else:
        print("FAIL: the gate did not detect a real branch")


def case_5_merge_revision_still_fails_the_gate(cfg: Config, heads: list[str]) -> None:
    _log("Case 5: reconciling the branch with `alembic merge` converges to one head, "
         "but the gate still (correctly) rejects it — a merge is not a linear chain")
    merged = command.merge(cfg, heads, message="merge branch A and B", rev_id="0005")
    script = ScriptDirectory.from_config(cfg)
    new_heads = script.get_heads()
    print(f"    merge revision: {merged.revision}, down_revision={merged.down_revision}")
    print(f"    heads after merge: {new_heads}")
    violations = linear_only_gate(script)
    if len(new_heads) == 1 and violations:
        print(
            "PASS: `alembic merge` restores a single head but the merge revision itself "
            "has a tuple down_revision — the gate still flags it correctly, confirming "
            "'one head' alone is NOT sufficient to prove linearity; the no-merge-revision "
            "check is required too"
        )
    else:
        print(f"UNEXPECTED: heads={new_heads} violations={violations}")


def case_6_delete_and_rebase_restores_linearity(root: Path, cfg: Config) -> None:
    _log("Case 6: the actual repair a linear-only policy forces — delete the errant branch "
         "file and rebase the surviving one, exactly as this repo would edit "
         "schema_version.py's dict in place rather than merge")
    versions_dir = root / "versions"
    for fname in list(versions_dir.glob("*0004b*")) + list(versions_dir.glob("*0005*")):
        fname.unlink()
    # 0004a still points at 0003 as its down_revision — already linear once
    # 0004b (the second child) and the merge revision are gone.
    script = ScriptDirectory.from_config(cfg)
    violations = linear_only_gate(script)
    heads = script.get_heads()
    print(f"    heads after removing the branch + merge: {heads}")
    print(f"    violations: {violations}")
    if not violations and heads == ["0004a"]:
        print("PASS: deleting the losing branch (not merging) restores a strictly "
              "linear chain, matching how schema_version.py's ledger would be repaired "
              "(the dict has no branch to begin with)")
    else:
        print(f"UNEXPECTED: heads={heads} violations={violations}")


def case_7_single_depends_on_edge_evades_head_count(root: Path, cfg: Config) -> None:
    _log(
        "Case 7: a revision with a single `depends_on` edge — Alembic does not count "
        "this toward get_heads() or down_revision children, so does it evade a "
        "head-count-only gate while the corrected gate still catches it?"
    )
    command.revision(cfg, message="single depends_on", rev_id="0005dep", head="0004a", depends_on="0002")
    script = ScriptDirectory.from_config(cfg)
    heads = script.get_heads()
    print(f"    heads after adding a depends_on edge: {heads}")
    violations = linear_only_gate(script)
    print(f"    violations found: {len(violations)}")
    for v in violations:
        print(f"    - {v}")
    if len(heads) == 1 and violations:
        print(
            "PASS: get_heads() alone still reports exactly one head (depends_on adds no "
            "new head and is not a down_revision branch), but the corrected gate's "
            "dependencies check catches the extra edge that a head-count-only gate would miss"
        )
    else:
        print(f"UNEXPECTED: heads={heads} violations={violations}")
    for fname in (root / "versions").glob("*0005dep*"):
        fname.unlink()


def case_8_multiple_depends_on_edges_also_caught(root: Path, cfg: Config) -> None:
    _log("Case 8: a revision with multiple depends_on targets is also caught")
    command.revision(cfg, message="multi depends_on", rev_id="0006dep", head="0004a", depends_on=["0001", "0002"])
    script = ScriptDirectory.from_config(cfg)
    heads = script.get_heads()
    violations = linear_only_gate(script)
    print(f"    heads: {heads}")
    print(f"    violations found: {len(violations)}")
    for v in violations:
        print(f"    - {v}")
    if len(heads) == 1 and violations:
        print("PASS: multiple depends_on targets are also caught by the corrected gate")
    else:
        print(f"FAIL: multiple depends_on targets were not caught (heads={heads} violations={violations})")
    for fname in (root / "versions").glob("*0006dep*"):
        fname.unlink()


if __name__ == "__main__":
    import alembic

    print(f"alembic {alembic.__version__}")
    root = Path(tempfile.mkdtemp(prefix="pr13_alembic_"))
    try:
        cfg = build_env(root)
        last_linear_head = case_1_linear_chain_one_head(cfg)
        case_2b_concurrent_checkouts_bypass_unspliced_guard(root, last_linear_head)
        case_2_branch_creates_two_heads(cfg, last_linear_head)
        case_3_upgrade_head_singular_fails_with_multiple_heads(cfg)
        case_4_linear_only_gate_catches_the_branch(cfg)
        script = ScriptDirectory.from_config(cfg)
        heads_for_merge = sorted(script.get_heads())
        case_5_merge_revision_still_fails_the_gate(cfg, heads_for_merge)
        case_6_delete_and_rebase_restores_linearity(root, cfg)
        case_7_single_depends_on_edge_evades_head_count(root, cfg)
        case_8_multiple_depends_on_edges_also_caught(root, cfg)
        print("\nDone.")
    finally:
        shutil.rmtree(root, ignore_errors=True)

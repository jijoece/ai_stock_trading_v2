"""Turn the sentinel credential-safety run matrix into the committed record.

Reads result_S0..S5 and result_P1..P2 written by run_dotenv_sentinel.sh and
prints the report checked in as dotenv_sentinel_output.txt.

Every claim below is asserted against the measured JSON, so a regression fails
this script instead of being narrated around it. In particular the benign
credential-named defaults are asserted as an EXACT set: a LumiBot upgrade that
starts resolving one more credential-named variable fails here.

Exit status is non-zero on any failed assertion.
"""
from __future__ import annotations

import collections
import json
import pathlib
import sys

# Two independent leak paths, each with its own positive control.
DOTENV_LABELS = ["S0", "S1", "S2", "S3", "S4", "S5"]
PROCENV_LABELS = ["P1", "P2"]
LABELS = DOTENV_LABELS + PROCENV_LABELS

# Runs that must satisfy the full five-property credential-safety proof.
SAFE_LABELS = ["S0", "S2", "S3", "S4", "P2"]
# Runs deliberately left unprotected, to prove the sentinels are detectable.
CONTROL_LABELS = ["S1", "S5", "P1"]

BENIGN_LUMIBOT_DEFAULTS = {
    "COINBASE_SANDBOX",
    "IB_USE_PAPER_ACCOUNT",
    "DATADOWNLOADER_API_KEY_HEADER",
}

DESCRIPTIONS = {
    "S0": "baseline: clean CWD, no .env anywhere, suppression ON",
    "S1": "CONTROL: sentinel .env in CWD, suppression OFF",
    "S2": "sentinel .env in CWD, suppression ON",
    "S3": "identical repeat of S2 (determinism)",
    "S4": "S2 + final bar close perturbed +5.00",
    "S5": "CONTROL: CWD is an EMPTY subdirectory, suppression OFF",
    "P1": "CONTROL: fake creds inherited from the parent process, NO scrub",
    "P2": "fake creds inherited from the parent process, scrub ON",
}

FAILURES: list[str] = []


def check(condition: bool, message: str) -> None:
    """Record rather than raise, so one run prints every failure it found."""
    if not condition:
        FAILURES.append(message)


def main() -> int:
    out = pathlib.Path(sys.argv[1] if len(sys.argv) > 1 else "out")
    runs = {label: json.loads((out / f"result_{label}.json").read_text()) for label in LABELS}

    def emit(line: str = "") -> None:
        print(line)

    emit("=== LumiBot 4.5.78 credential-safety proof — raw evidence ===")
    emit(f"python: {runs['S0']['python']}   lumibot: {runs['S0']['lumibot_version']} "
         f"(asserted exactly 4.5.78: {runs['S0']['lumibot_version_is_exactly_4_5_78']})")
    emit("mechanisms under test:")
    emit("  .env / .env.local     -> LUMIBOT_DISABLE_DOTENV=1 before `import lumibot`")
    emit("  process environment   -> credential scrub of os.environ before the import")
    emit("sentinel tokens (fake; authenticate nothing):")
    emit("  SENTINEL-DOTENV-7f3a9c21e4b8    written into the .env / .env.local fixtures")
    emit("  SENTINEL-PROCENV-3d5b18ca9027   exported into the child process environment")
    emit()

    for label in LABELS:
        check(runs[label]["lumibot_version_is_exactly_4_5_78"],
              f"{label}: lumibot version is not exactly 4.5.78")
        check(runs[label]["backtest_ok"], f"{label}: backtest did not complete")

    emit("--- run matrix ---")
    emit(f"  {'run':<4} {'suppress':<9} {'scrub':<7} {'.env cwd':<9} {'inherited':<10} "
         f"{'leaked':<11} {'env vals':<9} {'net':<5} {'broker':<7} {'total_return'}")
    for label in LABELS:
        r = runs[label]
        scrub = {"absent": "on", "inherit": "off", "present": "seed"}[r["spike_creds_mode"]]
        emit(f"  {label:<4} {str(r['dotenv_suppressed']):<9} {scrub:<7} "
             f"{str(r['sentinel_dotenv_present_in_cwd']):<9} "
             f"{str(bool(r['procenv_sentinel_keys_inherited'])):<10} "
             f"{('LEAKED' if r['sentinel_values_loaded'] else 'no'):<11} "
             f"{len(r['credential_values_from_environment_total']):<9} "
             f"{r['network_attempt_count']:<5} "
             f"{('built' if not r['broker_is_none'] else 'None'):<7} "
             f"{r['backtest']['total_return']}")
    emit()
    for label in LABELS:
        emit(f"  {label} = {DESCRIPTIONS[label]}")
    emit()
    emit("  'env vals'  = credential-named variables whose value came from the process")
    emit("                environment (the strict metric; must be 0 in protected runs)")
    emit("  'leaked'    = either sentinel token found in os.environ after import")
    emit()

    emit("--- positive controls: the sentinels are real and detectable ---")
    for label in CONTROL_LABELS:
        r = runs[label]
        targets = collections.Counter(a["target"] for a in r["network_attempts_total"])
        emit(f"  {label}: {DESCRIPTIONS[label]}")
        emit(f"      sentinel keys in os.environ after import: {r['sentinel_env_keys_after_import']}")
        emit(f"      sentinel reached LumiBot configs: {r['sentinel_in_lumibot_configs']}")
        # Count only: a control inherits the operator's real ambient
        # environment, and this record has no business enumerating which
        # credential variables their machine happens to define.
        emit(f"      credential values from the environment: "
             f"{len(r['credential_values_from_environment_total'])} "
             f"(sentinel-carrying: {r['sentinel_env_keys_after_import']})")
        emit(f"      live broker object constructed at import: {not r['broker_is_none']}")
        emit(f"      blocked outbound attempts: {dict(targets)}")
        # A control that leaks nothing would silently invalidate every
        # protected run, so each control must actually demonstrate the hazard.
        check(r["sentinel_values_loaded"], f"{label}: control leaked nothing — sentinel is not detectable")
        check(bool(r["sentinel_in_lumibot_configs"]), f"{label}: control sentinel never reached a LumiBot config")
        check(not r["broker_is_none"], f"{label}: control built no broker")
        check(r["network_attempt_count"] > 0, f"{label}: control made no outbound attempt")
    emit()
    emit("  S1/S5 show the .env path: S5's CWD is EMPTY and the PARENT's .env still")
    emit("  loaded, because find_and_load_dotenv() walks upward to the filesystem")
    emit("  root — so chdir-to-an-empty-directory is NOT a suppression mechanism.")
    emit("  P1 shows the process-environment path: credentials inherited from the")
    emit("  parent reach LumiBot untouched when the scrub does not run.")
    emit()

    emit("--- protected runs: the five required properties ---")
    for label in SAFE_LABELS:
        r = runs[label]
        # 1. no broker credential VALUE available to or loaded by LumiBot
        from_env = r["credential_values_from_environment_total"]
        check(from_env == [], f"{label}: credential values came from the environment: {from_env}")
        # ...and the benign defaults are pinned to an exact set, so a new
        # credential-named resolution cannot slip through as narrative.
        with_values = set(r["credential_env_reads_with_values_total"])
        unexpected = sorted(with_values - BENIGN_LUMIBOT_DEFAULTS)
        missing = sorted(BENIGN_LUMIBOT_DEFAULTS - with_values)
        check(not unexpected, f"{label}: unexpected credential-named value(s): {unexpected}")
        check(not missing, f"{label}: expected benign default(s) absent: {missing} "
                           "(the check may no longer be measuring what it claims)")
        # 2. nothing loaded from a .env/.env.local or inherited from the environment
        check(not r["dotenv_sentinel_loaded"], f"{label}: .env sentinel loaded")
        check(r["dotenv_sentinel_keys_after_import"] == [], f"{label}: .env sentinel keys present")
        check(r["dotenv_sentinel_keys_after_run"] == [], f"{label}: .env sentinel appeared during run")
        check(not r["procenv_sentinel_survived"], f"{label}: inherited credential survived")
        check(r["procenv_sentinel_keys_after_import"] == [], f"{label}: procenv sentinel keys present")
        check(r["procenv_sentinel_keys_after_run"] == [], f"{label}: procenv sentinel appeared during run")
        check(r["sentinel_in_lumibot_configs"] == {}, f"{label}: sentinel in a LumiBot config")
        # 3. no broker / live data provider initialized
        check(r["broker_is_none"], f"{label}: broker was initialized")
        check(r["data_source_is_none"], f"{label}: data source was initialized")
        # 4. zero outbound network attempts
        check(r["network_attempt_count"] == 0, f"{label}: outbound attempt made")
    emit(f"  runs checked: {', '.join(SAFE_LABELS)}")
    emit("  credential values sourced from the environment:  none, in every run")
    emit("  credential-named variables resolving to a value:  exactly the 3 benign")
    emit("    LumiBot defaults, asserted as an exact set —")
    emit('      COINBASE_SANDBOX               os.environ.get(..., "false")   mode flag')
    emit('      IB_USE_PAPER_ACCOUNT           os.environ.get(..., "true")    mode flag')
    emit('      DATADOWNLOADER_API_KEY_HEADER  os.environ.get(..., "X-Downloader-Key")')
    emit("                                                                   header NAME")
    emit("    Attribution is measured, not interpreted: the tracer resolves key presence")
    emit("    separately from the value, so a default returned for an ABSENT key is")
    emit("    recorded as not-from-environment. Any fourth credential-named value, or")
    emit("    any of these arriving from the environment instead, fails this script.")
    emit("  .env / .env.local sentinel loaded:                never")
    emit("  inherited process-environment credential survived: never")
    emit("  sentinel in any LumiBot *_CONFIG:                 never")
    emit("  broker / data source initialized:                 never (both None)")
    emit("  outbound network attempts:                        0")
    emit()
    r = runs["S2"]
    emit(f"  for scale: LumiBot LOOKED FOR {len(r['credential_env_reads_total'])} "
         "credential-named variables in S2.")
    emit("  Reads are unavoidable and are not the safety property; values are.")
    emit()

    emit("--- the process-environment case in detail (P1 control vs P2) ---")
    for label in ("P1", "P2"):
        r = runs[label]
        emit(f"  {label}: inherited from parent        {r['procenv_sentinel_keys_inherited']}")
        emit(f"      after the scrub (pre-import)  {r['procenv_sentinel_keys_after_scrub']}")
        emit(f"      after import                  {r['procenv_sentinel_keys_after_import']}")
        emit(f"      after the backtest            {r['procenv_sentinel_keys_after_run']}")
        emit(f"      LumiBot configs               {r['sentinel_in_lumibot_configs']}")
        emit(f"      broker built / outbound       {not r['broker_is_none']} / "
             f"{r['network_attempt_count']}")
    # Both must genuinely inherit, or P2 proves nothing.
    for label in ("P1", "P2"):
        check(bool(runs[label]["procenv_sentinel_keys_inherited"]),
              f"{label}: inherited no sentinel credentials — the case is not being exercised")
    check(runs["P1"]["procenv_sentinel_keys_after_scrub"] != [],
          "P1: control must retain the inherited credentials (no scrub)")
    check(runs["P2"]["procenv_sentinel_keys_after_scrub"] == [],
          "P2: scrub did not remove the inherited credentials before import")
    check(runs["P2"]["sentinel_dotenv_present_in_cwd"] is False,
          "P2: CWD held a .env — the environment must be the only credential source")
    emit()
    emit("  P1 and P2 inherit identical credentials in a CWD with no .env, so the")
    emit("  environment is the only possible source and the scrub is the only")
    emit("  difference between them.")
    emit()

    emit("--- determinism and caller-supplied data ---")
    det = runs["S2"]["backtest"] == runs["S3"]["backtest"]
    same_as_clean = runs["S2"]["backtest"] == runs["S0"]["backtest"]
    same_as_procenv = runs["P2"]["backtest"] == runs["S0"]["backtest"]
    moved = runs["S4"]["backtest"] != runs["S2"]["backtest"]
    check(det, "S2 != S3: repeated offline run was not deterministic")
    check(same_as_clean, "S2 != S0: presence of a .env changed the result")
    check(same_as_procenv, "P2 != S0: inherited credentials changed the result")
    check(moved, "S4 == S2: perturbing an input bar did not change the result")
    emit(f"  S2 == S3 (full result dict, repeated offline run):  {det}")
    emit(f"  S2 == S0 (sentinel .env present vs absent):         {same_as_clean}")
    emit(f"  P2 == S0 (inherited credentials vs none):           {same_as_procenv}")
    emit(f"  S4 != S2 (one input bar perturbed):                 {moved}")
    emit(f"    S2/S3 input digest: {runs['S2']['input_bars_digest']}")
    emit(f"    S4    input digest: {runs['S4']['input_bars_digest']}")
    emit(f"    total_return  S2 {runs['S2']['backtest']['total_return']}"
         f"  ->  S4 {runs['S4']['backtest']['total_return']}")
    emit("  The backtest consumed only the caller-supplied 10-bar fixture: neither a")
    emit("  .env nor an inherited credential changed anything, and perturbing one bar")
    emit("  changed the result.")
    emit()

    if FAILURES:
        emit(f"FAILED — {len(FAILURES)} assertion(s):")
        for failure in FAILURES:
            emit(f"  - {failure}")
        return 1
    emit("ALL ASSERTIONS PASSED")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())

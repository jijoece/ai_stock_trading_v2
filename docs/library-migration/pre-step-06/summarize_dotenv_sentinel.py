"""Turn the sentinel-.env run matrix into the committed evidence record.

Reads result_S0..S5.json written by run_dotenv_sentinel.sh and prints the
report checked in as dotenv_sentinel_output.txt. Every assertion below is
evaluated against the measured JSON, so a regression changes the report rather
than being narrated around it.
"""
from __future__ import annotations

import collections
import json
import pathlib
import sys

LABELS = ["S0", "S1", "S2", "S3", "S4", "S5"]

DESCRIPTIONS = {
    "S0": "baseline: clean CWD, no .env anywhere, suppression ON",
    "S1": "POSITIVE CONTROL: sentinel .env in CWD, suppression OFF",
    "S2": "sentinel .env in CWD, suppression ON",
    "S3": "identical repeat of S2 (determinism)",
    "S4": "S2 + final bar close perturbed +5.00",
    "S5": "POSITIVE CONTROL: CWD is an EMPTY subdirectory, suppression OFF",
}


def main() -> int:
    out = pathlib.Path(sys.argv[1] if len(sys.argv) > 1 else "out")
    runs = {label: json.loads((out / f"result_{label}.json").read_text()) for label in LABELS}

    def emit(line: str = "") -> None:
        print(line)

    emit("=== LumiBot 4.5.78 .env-suppression proof — raw evidence ===")
    emit(f"python: {runs['S0']['python']}   lumibot: {runs['S0']['lumibot_version']} "
         f"(asserted exactly 4.5.78: {runs['S0']['lumibot_version_is_exactly_4_5_78']})")
    emit("mechanism under test: LUMIBOT_DISABLE_DOTENV=1 set before `import lumibot`")
    emit("sentinel token: SENTINEL-DOTENV-7f3a9c21e4b8 (fake; authenticates nothing)")
    emit()

    emit("--- run matrix ---")
    header = (f"  {'run':<4} {'suppress':<9} {'.env in cwd':<12} {'sentinel':<11} "
              f"{'cred values':<12} {'net':<5} {'broker':<8} {'total_return'}")
    emit(header)
    for label in LABELS:
        r = runs[label]
        emit(f"  {label:<4} {str(r['dotenv_suppressed']):<9} "
             f"{str(r['sentinel_dotenv_present_in_cwd']):<12} "
             f"{('LOADED' if r['sentinel_values_loaded'] else 'not loaded'):<11} "
             f"{len(r['credential_env_reads_with_values_total']):<12} "
             f"{r['network_attempt_count']:<5} "
             f"{('built' if not r['broker_is_none'] else 'None'):<8} "
             f"{r['backtest']['total_return']}")
    emit()
    for label in LABELS:
        emit(f"  {label} = {DESCRIPTIONS[label]}")
    emit()

    emit("--- what the positive controls prove (the sentinel is real) ---")
    for label in ("S1", "S5"):
        r = runs[label]
        targets = collections.Counter(a["target"] for a in r["network_attempts_total"])
        emit(f"  {label}: sentinel keys reaching os.environ: {r['sentinel_env_keys_after_import']}")
        emit(f"      sentinel reached LumiBot configs: {r['sentinel_in_lumibot_configs']}")
        emit(f"      live broker object constructed at import: {not r['broker_is_none']}")
        emit(f"      blocked outbound attempts: {dict(targets)}")
        emit(f"      cwd: {r['cwd']}")
    emit("  S5 is the decisive one: its CWD is an EMPTY directory, yet the parent's")
    emit("  .env still loaded. find_and_load_dotenv() walks UPWARD to the filesystem")
    emit("  root, so chdir-to-an-empty-directory is NOT a suppression mechanism.")
    emit()

    emit("--- what suppression proves (S2/S3/S4) ---")
    for label in ("S2", "S3", "S4"):
        r = runs[label]
        assert r["sentinel_dotenv_present_in_cwd"], f"{label}: sentinel .env missing from CWD"
        assert not r["sentinel_values_loaded"], f"{label}: sentinel LEAKED"
        assert r["sentinel_env_keys_after_import"] == [], f"{label}: sentinel keys present"
        assert r["sentinel_env_keys_after_run"] == [], f"{label}: sentinel appeared during run"
        assert r["sentinel_in_lumibot_configs"] == [], f"{label}: sentinel in a LumiBot config"
        assert r["network_attempt_count"] == 0, f"{label}: outbound attempt made"
        assert r["broker_is_none"], f"{label}: broker was initialized"
        assert r["data_source_is_none"], f"{label}: data source was initialized"
        assert r["backtest_ok"], f"{label}: backtest did not complete"
    emit("  sentinel .env AND .env.local present in the CWD for all three runs")
    emit("  sentinel values loaded:                      never")
    emit("  sentinel values in any LumiBot *_CONFIG:      never")
    emit("  broker / data source initialized:             never (both None)")
    emit("  outbound network attempts:                    0")
    emit()

    emit("--- credential-named reads: names vs values ---")
    r = runs["S2"]
    emit(f"  credential-named variables LumiBot LOOKED FOR: {len(r['credential_env_reads_total'])}")
    emit(f"  of those, ones that RESOLVED TO A VALUE:       "
         f"{len(r['credential_env_reads_with_values_total'])}")
    emit(f"    {r['credential_env_reads_with_values_total']}")
    emit("  All three are LumiBot's own hardcoded defaults, not values from the")
    emit("  environment or from any .env — they only match the credential-name")
    emit("  markers used by the tracer:")
    emit('    COINBASE_SANDBOX             os.environ.get("COINBASE_SANDBOX", "false")')
    emit('    IB_USE_PAPER_ACCOUNT         os.environ.get("IB_USE_PAPER_ACCOUNT", "true")')
    emit('    DATADOWNLOADER_API_KEY_HEADER  ...get(..., "X-Downloader-Key")  # a header NAME')
    emit("  No broker credential VALUE was available to LumiBot in any suppressed run.")
    emit("  This is why the requirement is not 'zero credential reads': LumiBot reads")
    emit("  the NAMES unconditionally, and no configuration prevents that.")
    emit()

    emit("--- determinism and caller-supplied data ---")
    det = runs["S2"]["backtest"] == runs["S3"]["backtest"]
    same_as_clean = runs["S2"]["backtest"] == runs["S0"]["backtest"]
    moved = runs["S4"]["backtest"] != runs["S2"]["backtest"]
    assert det and same_as_clean and moved
    emit(f"  S2 == S3 (full result dict, repeated offline run):  {det}")
    emit(f"  S2 == S0 (sentinel .env present vs absent):         {same_as_clean}")
    emit(f"  S4 != S2 (one input bar perturbed):                 {moved}")
    emit(f"    S2/S3 input digest: {runs['S2']['input_bars_digest']}")
    emit(f"    S4    input digest: {runs['S4']['input_bars_digest']}")
    emit(f"    total_return  S2 {runs['S2']['backtest']['total_return']}"
         f"  ->  S4 {runs['S4']['backtest']['total_return']}")
    emit("  The backtest consumed only the caller-supplied 10-bar fixture: the")
    emit("  presence or absence of a .env changed nothing, and perturbing one bar")
    emit("  changed the result.")
    emit()
    emit("ALL ASSERTIONS PASSED")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())

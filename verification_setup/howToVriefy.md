# OSIRIS Verification Setup

Read-only DRC/LVS verification harness for SKY130A layouts and schematics. It runs
Magic for DRC and extraction, and Netgen for LVS, then writes fail-closed evidence
under `results/`.

This is an automated screening flow, **not** production sign-off.

## Contents

| Path | Purpose |
| --- | --- |
| `Validator/v0_validator.py` | Main DRC + LVS validator (Magic/Netgen orchestration) |
| `Adapter/adapter_v0.py` | SPICE normalization used by the validator |
| `DRC_Checker_Probe/drc_checker_probe.py` | Independent Magic-only DRC diagnostic against a completed run |
| `data/` | Flattened target-test dataset (see `data/README.md`) |
| `results/` | Verification evidence from previous runs |
| `tools/pdk/sky130A` | Pinned SKY130A PDK used by the flow |
| `archive/` | Old cleanup backups |

Environment used by the existing evidence:

- Magic `8.3.691` (`/usr/local/bin/magic`)
- Netgen in `PATH` (`/usr/local/bin/netgen`)
- PDK `tools/pdk/sky130A` (also available under `~/.volare/...`)

Run all commands from the repository root:

```bash
cd /home/haytham/gp/verification_setup
```

## Two input modes

| Input tree | Mode | Flag |
| --- | --- | --- |
| One GDS + one SPICE file | Direct | `--gds` + `--schematic` |
| Osiris-shaped dataset with a selection CSV | Batch | `--selected` + `--dataset-root` |

**The checked-in `data/` tree is flattened and uses direct mode.** Do not drive it
with `--selected data/selected_layouts.csv`; that CSV points at
`/home/haytham/gp/Osiris_Dataset`. See `data/README.md` for the data-specific
commands.

Batch mode requires this shape under `--dataset-root`:

```text
<root>/data/layouts/<family>/<circuit>/<variant>/<FILE>.gds
<root>/netlists/<family>/<circuit>.sp
```

## Full DRC + LVS, single design (direct mode)

```bash
python3 Validator/v0_validator.py \
  --gds data/layouts/five_transistors_ota/five_transistors_ota_310/variant_0.gds \
  --schematic data/schematics/five_transistors_ota/five_transistors_ota_310/variant_0.sp \
  --source-dim-units meters \
  --width-mode per-finger \
  --pdk tools/pdk/sky130A \
  --out results/data_310_v0_full \
  --timeout 300
```

Direct mode requires explicit `--source-dim-units` and `--width-mode`; the
validator never guesses them.

What runs:

1. Magic reads the GDS and loads the top cell.
2. Magic runs `drc(full)` and reports a violation count.
3. Magic extracts the layout and creates `extracted_layout.spice` with ports.
4. Port and MOS W/L geometry audits run.
5. Netgen compares the extracted netlist against the normalized schematic.

Useful options:

```bash
# LVS only: skips DRC, extraction geometry audit is NOT_TESTED
python3 Validator/v0_validator.py \
  --gds data/layouts/five_transistors_ota/five_transistors_ota_310/variant_0.gds \
  --schematic data/schematics/five_transistors_ota/five_transistors_ota_310/variant_0.sp \
  --source-dim-units meters --width-mode per-finger \
  --pdk tools/pdk/sky130A \
  --out results/data_310_v0_lvs \
  --lvs-only

# Parse/adapt only: no Magic or Netgen execution
python3 Validator/v0_validator.py \
  --gds data/layouts/five_transistors_ota/five_transistors_ota_310/variant_0.gds \
  --schematic data/schematics/five_transistors_ota/five_transistors_ota_310/variant_0.sp \
  --source-dim-units meters --width-mode per-finger \
  --pdk tools/pdk/sky130A \
  --out /tmp/data_310_dry \
  --dry-run

# Explicit cell names if inference is ever ambiguous
--layout-top FIVE_TRANSISTORS_OTA_310_0
--schematic-top five_transistors_ota_310
```

## Batch mode (Osiris-shaped dataset)

```bash
python3 Validator/v0_validator.py \
  --selected "$HOME/gp/osiris_selection_report/selected_layouts.csv" \
  --dataset-root "$HOME/gp/Osiris_Dataset" \
  --pdk tools/pdk/sky130A \
  --out results/osiris_pilot \
  --mode pilot
```

- `--mode pilot` (default): one variant per family.
- `--mode all`: every row in the CSV.
- `--family NAME` (repeatable): restrict to one or more families.
- If `--selected` is omitted, the default is
  `~/gp/osiris_selection_report/selected_layouts.csv`.

`--out` must be outside `--dataset-root`.

## DRC-only diagnostics

There is no `--drc-only` switch. A full run already reports `drc` and
`drc_count`. To get rule classes and confirm the count independently, run the
probe on a completed full run:

```bash
python3 DRC_Checker_Probe/drc_checker_probe.py \
  --root results/data_310_v0_full \
  --pdk tools/pdk/sky130A \
  --out results/drc_probe_310_v0 \
  --case direct/five_transistors_ota_310/input
```

The probe reads `validation.json` from the baseline run, re-runs Magic DRC in an
isolated scratch directory, and reports violation classes. It does not modify the
GDS, SPICE, PDK, or baseline report.

- `--case` is relative to `--root`; repeat it to select multiple cases.
- Without `--case`, one case per family is selected (up to `--max-cases`, default 2).
- `--compare-original-import` also tries the original dataset import options.

## Outputs

Direct mode:

```text
<out>/direct/<schematic-subckt>/input/validation.json
<out>/direct/<schematic-subckt>/input/magic.log
<out>/direct/<schematic-subckt>/input/netgen_comp.out
<out>/direct/<schematic-subckt>/input/extracted_layout.spice
<out>/direct/<schematic-subckt>/input/adapter_lvs.spice
<out>/direct/<schematic-subckt>/input/geometry_audit.json
```

Batch mode:

```text
<out>/<family>/<circuit>/<variant>/validation.json
```

Both modes also write `<out>/environment_lock.json`, `<out>/results.csv`, and
`<out>/summary.json`.

`validation.json` is the primary record. The most useful fields are:

- `drc` / `drc_count` — Magic DRC verdict and number of violations
- `lvs` — Netgen verdict
- `geometry` — MOS W/L multiset audit
- `port_audit` — extracted top-level ports vs source ports
- `import` — Magic PDK/import confidence (`PASS`, `REVIEW_REQUIRED`, `UNPROVEN`)
- `mos_finger_audit` — `PASS` only when no multi-finger normalization is needed
- `lvs_verified` — LVS passed and sources were unchanged during the run
- `status` — overall case verdict

## Verdicts

| Value | Meaning |
| --- | --- |
| `PASS` | Check completed and found no violations/mismatch |
| `FAIL` | Violations or a netlist/geometry mismatch were found |
| `UNPROVEN` | Evidence is incomplete or the flow failed closed |
| `TOOL_ERROR` | Magic/Netgen or the PDK failed to run |
| `NOT_TESTED` | Check was skipped (for example DRC under `--lvs-only`) |
| `GOLDEN_CANDIDATE` | All automated screens passed; not independently approved sign-off |

The validator exits `0` only when every case is a golden candidate (or
`LVS_PASS` under `--lvs-only`). Exit code `2` is normal when cases ran correctly
but did not pass every screen; inspect the reports before treating it as a crash.

## Scope and limitations

- SKY130A only.
- Source schematics must contain exactly one `.subckt` and MOS devices using
  `sky130_fd_pr__nfet_*` / `sky130_fd_pr__pfet_*`.
- `nf > 1` finger semantics are recorded, but `mos_finger_audit` remains
  `UNPROVEN`; a full run will not become a golden candidate.
- Resistors are audited, not electrically certified.
- Unsupported non-MOS devices are recorded as `UNPROVEN`, not silently accepted.
- Input GDS and SPICE files are hashed before and after each run. A hash change
  invalidates `lvs_verified`.
- Outputs must stay outside the input source directories. Use `results/` or `/tmp`.

## Calibration case

With the pinned tools in this repository, `five_transistors_ota_310/variant_0`
has previously produced:

- LVS `PASS`
- DRC `FAIL` with 175 violations (`met2.2` = 93, `li.3` = 74)
- Geometry `FAIL` (the source uses `nf=10..16`)
- Overall `status = FAIL`

If you reproduce those numbers, the local flow matches the stored evidence.

# Testing the `data/` folder

This folder is a flattened target-test subset: 2 families, 7 circuits per family,
and 2 layout variants per circuit (28 GDS + 28 SPICE files).

```text
data/
├── layouts/
│   ├── five_transistors_ota/
│   │   ├── five_transistors_ota_155/
│   │   │   ├── variant_0.gds
│   │   │   └── variant_100.gds
│   │   └── ...
│   └── ota_ff/
│       ├── ota_ff_1002/
│       │   ├── variant_0.gds
│       │   └── variant_100.gds
│       └── ...
├── schematics/
│   ├── five_transistors_ota/
│   │   └── five_transistors_ota_155/
│   │       ├── variant_0.sp
│   │       └── variant_100.sp
│   └── ota_ff/
│       └── ...
└── selected_layouts.csv   # NOT valid for this folder
```

Each GDS/SPICE pair shares the same relative path and basename:

```text
data/layouts/<family>/<circuit>/<variant>.gds
data/schematics/<family>/<circuit>/<variant>.sp
```

The schematic in a variant pair is identical; only the layout differs.

## Why not batch mode?

The validator's batch CSV mode assumes the original Osiris tree:

```text
<root>/data/layouts/<family>/<circuit>/<variant>/<FILE>.gds
<root>/netlists/<family>/<circuit>.sp
```

The files here are stored as `<family>/<circuit>/<variant>.gds`, so batch mode
rejects them with a path-shape error. In addition, `data/selected_layouts.csv`
is a byte-identical copy of
`/home/haytham/gp/osiris_selection_report/selected_layouts.csv`; every row points
at `/home/haytham/gp/Osiris_Dataset`, not at this folder.

Use direct mode (`--gds` + `--schematic`) for everything in `data/`.

Run commands from the repository root:

```bash
cd /home/haytham/gp/verification_setup
```

## One case: full DRC + LVS

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

This runs Magic DRC, Magic extraction, port/geometry audits, and Netgen LVS.

For LVS only:

```bash
python3 Validator/v0_validator.py \
  --gds data/layouts/five_transistors_ota/five_transistors_ota_310/variant_0.gds \
  --schematic data/schematics/five_transistors_ota/five_transistors_ota_310/variant_0.sp \
  --source-dim-units meters \
  --width-mode per-finger \
  --pdk tools/pdk/sky130A \
  --out results/data_310_v0_lvs \
  --lvs-only
```

To check the adapter/parsing before launching tools:

```bash
python3 Validator/v0_validator.py \
  --gds data/layouts/five_transistors_ota/five_transistors_ota_310/variant_0.gds \
  --schematic data/schematics/five_transistors_ota/five_transistors_ota_310/variant_0.sp \
  --source-dim-units meters \
  --width-mode per-finger \
  --pdk tools/pdk/sky130A \
  --out /tmp/data_310_dry \
  --dry-run
```

## All 28 cases

Run this from the repository root. Do **not** use `set -e`: the validator exits
`2` when a case ran correctly but did not pass every screen. That is a normal
verdict, not a crash.

```bash
cd /home/haytham/gp/verification_setup

out="results/data_sweep_$(date +%Y%m%d_%H%M%S)"
mkdir -p "$out"

for gds in data/layouts/*/*/*.gds; do
  fam=$(basename "$(dirname "$(dirname "$gds")")")
  cir=$(basename "$(dirname "$gds")")
  var=$(basename "$gds" .gds)
  sp="data/schematics/$fam/$cir/$var.sp"

  if [ ! -f "$sp" ]; then
    echo "SKIP $gds (missing $sp)"
    continue
  fi

  echo "=== $fam / $cir / $var ==="
  python3 -u Validator/v0_validator.py \
    --gds "$gds" \
    --schematic "$sp" \
    --source-dim-units meters \
    --width-mode per-finger \
    --pdk tools/pdk/sky130A \
    --out "$out/$fam/$cir/$var" \
    --timeout 300

  rc=$?
  echo "exit=$rc (2 = ran but not GOLDEN_CANDIDATE; inspect the report)"
done
```

Each case has its own output directory, so no `results.csv` is overwritten by a
later case:

```text
<out>/<family>/<circuit>/<variant>/direct/<circuit>/input/validation.json
```

For an LVS-only sweep, add `--lvs-only` and omit the DRC fields when reading the
reports.

### Aggregate the sweep

```bash
# Pick the newest sweep (or set out=results/data_sweep_YYYYMMDD_HHMMSS)
out=$(ls -dt results/data_sweep_* | head -1)

python3 - "$out" <<'PY'
import json
import sys
from pathlib import Path

root = Path(sys.argv[1])
rows = []
for path in sorted(root.glob("*/*/*/direct/*/input/validation.json")):
    rows.append(json.loads(path.read_text(encoding="utf-8")))

print(f"{'circuit':28} {'variant':12} {'status':10} {'drc':10} {'count':>5} {'lvs':6} {'geom':6}")
for d in rows:
    print(f"{d['circuit_id']:28} {d['variant_id']:12} {d['status']:10} "
          f"{d['drc']:10} {str(d.get('drc_count', '')):>5} {d['lvs']:6} {d['geometry']:6}")
print(f"cases={len(rows)}")
PY
```

## DRC-only diagnostics

There is no `--drc-only` flag. Read `drc` / `drc_count` from a full run, or use
the DRC probe on that completed run to get violation classes:

```bash
# Set run to the specific sweep directory that contains the case
run=results/data_sweep_YYYYMMDD_HHMMSS

python3 DRC_Checker_Probe/drc_checker_probe.py \
  --root "$run/five_transistors_ota/five_transistors_ota_310/variant_0" \
  --pdk tools/pdk/sky130A \
  --out results/drc_probe_310_v0 \
  --case direct/five_transistors_ota_310/input
```

The probe requires the baseline `validation.json` with a DRC `PASS` or `FAIL`.
Its `--case` value is relative to `--root`.

## Reading a result

`<out>/.../validation.json` contains the verdict and evidence paths. Important
fields:

| Field | Meaning |
| --- | --- |
| `drc` | `PASS`, `FAIL`, `UNPROVEN`, or `TOOL_ERROR` |
| `drc_count` | Number of Magic DRC violations |
| `lvs` | Netgen result (`PASS`, `FAIL`, `UNPROVEN`, `TOOL_ERROR`) |
| `geometry` | MOS W/L multiset comparison |
| `port_audit` | Extracted ports vs original `.subckt` ports |
| `mos_finger_audit` | `UNPROVEN` when the source uses `nf > 1` |
| `import` | Magic import confidence |
| `status` | Overall case verdict |

Companion files:

```text
magic.log            Magic DRC/extraction log
netgen_comp.out      Detailed Netgen comparison
geometry_audit.json  Source vs extracted W/L signatures
adapter_lvs.spice    Normalized schematic used for LVS
extracted_layout.spice
```

## Expected calibration result

With the pinned PDK and tools in this repository, the known result for
`five_transistors_ota_310/variant_0` is:

- LVS: `PASS`
- DRC: `FAIL`, 175 violations (`met2.2` = 93, `li.3` = 74)
- Geometry: `FAIL` because the source uses `nf=10..16`
- Overall: `FAIL`

If a fresh run reproduces those values, the local flow is behaving like the
stored evidence. Do not “fix” the DRC count by changing import options unless
you are deliberately testing that hypothesis.

## Notes

- Always pass `--pdk tools/pdk/sky130A`; this machine also has a volare copy, so
  auto-detection can be ambiguous.
- Keep `--out` under `results/` (or `/tmp`), never inside `data/`.
- All schematics in this folder use meter dimensions, so
  `--source-dim-units meters` is correct.
- `--width-mode per-finger` matches the existing dataset convention
  (`w` is per finger and `nf` is separate). If a source's `w` is already total
  width, use `--width-mode total` instead; do not mix conventions.

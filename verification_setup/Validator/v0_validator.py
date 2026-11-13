#!/usr/bin/env python3
"""LVS Validator V0 (independent validator implementation).

This file contains the V0 validation, Magic/Netgen orchestration, GDS/geometry
checks, report generation, and fail-closed Netgen classifier.  The SPICE
normalizer is intentionally delegated to the standalone sibling component:
  ../Adapter/adapter_v0.py

The pre-rename baseline is preserved separately. Input GDS/SPICE are read-only.
Output evidence is written under --out, which must be outside input trees.

Scope: SKY130A; not an electrical-equivalence certification for nf>1, resistors
or arbitrary non-MOS devices. The original CLI behavior is retained.
"""
# Dataset provenance and compatibility notes: see ../DATA_SOURCES.md.
from __future__ import annotations

import argparse
import csv
import json
import os
import re
import shutil
import struct
import subprocess
import sys
from collections import Counter, defaultdict
from decimal import Decimal
from importlib.util import module_from_spec, spec_from_file_location
from pathlib import Path

# Explicit component boundary: load ONLY our standalone Adapter v0, never the
# combined baseline. Fail closed when the adapter component is unavailable.
_ADAPTER_FILE = Path(__file__).resolve().parent.parent / "Adapter" / "adapter_v0.py"
if not _ADAPTER_FILE.is_file():
    raise ImportError(f"Standalone Adapter v0 is missing: {_ADAPTER_FILE}")
_adapter_spec = spec_from_file_location("_spice_adapter_v0", _ADAPTER_FILE)
if _adapter_spec is None or _adapter_spec.loader is None:
    raise ImportError(f"Cannot load standalone Adapter v0: {_ADAPTER_FILE}")
_adapter = module_from_spec(_adapter_spec)
_adapter_spec.loader.exec_module(_adapter)

adapt_spice = _adapter.adapt_spice
file_sha256 = _adapter.file_sha256
flatten_spice = _adapter.flatten_spice
format_decimal = _adapter.format_decimal
parse_numeric = _adapter.parse_numeric
resistor_inventory = _adapter.resistor_inventory
spice_resistance_ohms = _adapter.spice_resistance_ohms
verify_mim_pdk_contract = _adapter.verify_mim_pdk_contract
IDENT = _adapter.IDENT
PROPERTY = _adapter.PROPERTY
VALID_MOS = _adapter.VALID_MOS
MIM_C_MODEL = _adapter.MIM_C_MODEL

DEFAULT_ROOT = Path.home() / "gp" / "Osiris_Dataset"
FATAL_MAGIC = re.compile(r"(?i)unknown layer/datatype|input off lambda grid|boundary was redefined")
MISMATCH = re.compile(r"(?i)top level cell failed pin matching|property errors were found|netlists do not match|circuits do not match|device mismatches|net mismatches|failed pin matching")
FINAL_MATCH = re.compile(r"(?im)^\s*Final result:\s*(?:Circuits|Netlists) match uniquely\s*\.")
FINAL_LINE = re.compile(r"(?im)^\s*Final result:\s*(.+)$")

def dump_json(path, obj):
    Path(path).write_text(json.dumps(obj, indent=2, ensure_ascii=False) + "\n", encoding="utf-8")


def import_event_details(log):
    """Preserve warning evidence without assuming an unknown layer is harmless."""
    details = []
    for line in log.splitlines():
        if not FATAL_MAGIC.search(line):
            continue
        item = {"message": line.strip()}
        cell = re.search(r'cell "([^"]+)"', line)
        layer = re.search(r"layer=(\d+)\s+type=(\d+)", line)
        if cell:
            item["cell"] = cell.group(1)
        if layer:
            item.update(layer=int(layer.group(1)), datatype=int(layer.group(2)))
        details.append(item)
    return details


def extracted_resistor_models(path):
    """Report model names in Magic extraction; never infer equivalence from equal pins."""
    statements = flatten_spice(Path(path).read_text(encoding="utf-8", errors="replace"))
    found = resistor_inventory(statements, extracted=True)
    return [{"instance": item["instance"], "model": item["model"], "nodes": item["nodes"]}
            for item in found]


def gds_top_cells(path):
    """Read only GDS STRNAME/SNAME records; no geometry modifications or dependencies."""
    structures, references = set(), set()
    with path.open("rb") as stream:
        while True:
            hdr = stream.read(4)
            if not hdr:
                break
            if len(hdr) != 4:
                raise ValueError("Truncated GDS record header")
            n, tag, dtype = struct.unpack(">HBB", hdr)
            if n < 4 or n % 2:
                raise ValueError(f"Invalid GDS record length {n}")
            payload = stream.read(n - 4)
            if len(payload) != n - 4:
                raise ValueError("Truncated GDS record body")
            if tag in (0x06, 0x12):  # STRNAME and SNAME
                name = payload.rstrip(b"\0").decode("ascii", errors="replace")
                (structures if tag == 0x06 else references).add(name)
    if not structures:
        raise ValueError("No GDS structures found")
    roots = sorted(structures - references)
    if not roots:
        raise ValueError("No top-level GDS structure found")
    return roots


def choose_top(path):
    roots = gds_top_cells(path)
    if len(roots) == 1:
        return roots[0], roots
    exact = [r for r in roots if r.casefold() == path.stem.casefold()]
    if len(exact) == 1:
        return exact[0], roots
    raise ValueError(f"Ambiguous GDS top cells {roots}; no exact basename match")


def source_geom_signature(devs):
    # Parallel devices can be merged by Netgen; group them BEFORE comparing W/L.
    groups = defaultdict(Decimal)
    lengths = {}
    for d in devs:
        drain, gate, source, bulk = d["terminals"]
        key = (d["model"], gate, bulk, tuple(sorted([drain, source])), format_decimal(d["l"]))
        groups[key] += d["w"]
        lengths[key] = d["l"]
    sig = Counter()
    for key, width in groups.items():
        sig[(key[0], format_decimal(width), format_decimal(lengths[key]))] += 1
    return sig


def extracted_geom_signature(path):
    """Collect geometry from Magic fingers, grouped by (model, gate, bulk, D/S unordered).

    Check the multiset of equivalent W/L per device; Netgen independently
    checks electrical net/pin matching. This does not claim Spice simulation
    equivalence for arbitrary nf conventions.
    """
    statements = flatten_spice(path.read_text(encoding="utf-8", errors="replace"))
    groups = defaultdict(lambda: [Decimal(0), None])
    for s in statements:
        tok = s.split()
        if not tok or tok[0][0].upper() not in ("X", "M") or len(tok) < 8:
            continue
        if not VALID_MOS.fullmatch(tok[5]):
            continue
        props = {}
        for token in tok[6:]:
            m = PROPERTY.fullmatch(token)
            if m:
                props[m.group(1).lower()] = m.group(2)
        if "w" not in props or "l" not in props:
            raise ValueError(f"Extracted MOS missing w/l: {tok[0]}")
        # Magic SKY130A emits micrometre widths/lengths in ext2spice lvs mode.
        w = parse_numeric(props["w"])
        l = parse_numeric(props["l"])
        model = tok[5].lower()
        d, g, src, b = tok[1:5]
        key = (model, g, b, tuple(sorted([d, src])), format_decimal(l))
        groups[key][0] += w
        groups[key][1] = l
    if not groups:
        raise ValueError("No extracted MOS recognized")
    sig = Counter()
    for key, (width, length) in groups.items():
        sig[(key[0], format_decimal(width), format_decimal(length))] += 1
    return sig, len(groups)


def compare_geometry(source_sig, extracted_sig):
    return (source_sig == extracted_sig,
            {"source": [[*k, v] for k, v in sorted(source_sig.items())],
             "extracted": [[*k, v] for k, v in sorted(extracted_sig.items())]})


def magic_tcl_escape(s):
    return '"' + str(s).replace('\\', '\\\\').replace('"', '\\"').replace('$', '\\$').replace('[', '\\[').replace(']', '\\]') + '"'


def make_magic_tcl(gds, top, extracted, ports, lvs_only=False):
    # Matches the successful manual Magic flow on five_transistors_ota_310.
    # Source ports are taken from original source .subckt, in original order.
    if not ports or len(set(ports)) != len(ports):
        raise ValueError("Source .subckt ports must be nonempty and unique")
    if any(not IDENT.fullmatch(pin) for pin in ports):
        raise ValueError(f"Unsupported top port names: {ports!r}")
    script = f'''# Generated LVS runner, read-only input GDS.
if {{[catch {{gds read {magic_tcl_escape(gds)}}} e]}} {{puts "GOLDEN_ERROR=GDS_READ:$e"; exit 20}}
if {{[catch {{load {magic_tcl_escape(top)}}} e]}} {{puts "GOLDEN_ERROR=LOAD:$e"; exit 21}}
if {{[tech name] ne "sky130A"}} {{puts "GOLDEN_ERROR=WRONG_TECH:[tech name]"; exit 22}}
puts "GOLDEN_TECH=[tech name]"
puts "GOLDEN_LOAD_OK=1"
'''
    if not lvs_only:
        script += '''if {[catch {drc style drc(full)} e]} {puts "GOLDEN_ERROR=DRC_STYLE:$e"; exit 23}
if {[catch {select top cell} e]} {puts "GOLDEN_ERROR=SELECT:$e"; exit 23}
if {[catch {snap internal} e]} {puts "GOLDEN_ERROR=SNAP:$e"; exit 23}
if {[catch {select bbox} bb]} {puts "GOLDEN_ERROR=BBOX:$bb"; exit 23}
if {[llength $bb] != 4 || [lindex $bb 0] >= [lindex $bb 2] || [lindex $bb 1] >= [lindex $bb 3]} {puts "GOLDEN_ERROR=EMPTY_BBOX:$bb"; exit 23}
if {[catch {box values {*}$bb} e]} {puts "GOLDEN_ERROR=BOX:$e"; exit 23}
puts "GOLDEN_DRC_BBOX=$bb"
if {[catch {drc check} e]} {puts "GOLDEN_ERROR=DRC_CHECK:$e"; exit 24}
if {[catch {drc catchup} e]} {puts "GOLDEN_ERROR=DRC_CATCHUP:$e"; exit 25}
if {[catch {drc listall count total} n]} {puts "GOLDEN_ERROR=DRC_COUNT:$n"; exit 26}
puts "GOLDEN_DRC_TOTAL=$n"
'''
    for idx, pin in enumerate(ports, start=1):
        safe = magic_tcl_escape(pin)
        script += f'''if {{[catch {{goto {safe}}} layer] || $layer eq ""}} {{puts "GOLDEN_ERROR=PORT_GOTO:{pin}"; exit 40}}
if {{[catch {{port {safe} make {idx}}} e]}} {{puts "GOLDEN_ERROR=PORT_MAKE:{pin}:$e"; exit 41}}
if {{[port {safe} index] ne "{idx}" || [port {idx} name] ne {safe}}} {{puts "GOLDEN_ERROR=PORT_VERIFY:{pin}"; exit 42}}
puts "GOLDEN_PORT={pin}:{idx}"
'''
    script += f'''puts "GOLDEN_PORTS_OK=1"
if {{[catch {{extract all}} e]}} {{puts "GOLDEN_ERROR=EXTRACT:$e"; exit 43}}
if {{[catch {{ext2spice lvs}} e]}} {{puts "GOLDEN_ERROR=EXT2SPICE_MODE:$e"; exit 44}}
if {{[catch {{ext2spice subcircuit top on}} e]}} {{puts "GOLDEN_ERROR=TOP_SUBCKT:$e"; exit 45}}
if {{[catch {{ext2spice -o {magic_tcl_escape(extracted)}}} e]}} {{puts "GOLDEN_ERROR=EXT2SPICE_OUTPUT:$e"; exit 46}}
puts "GOLDEN_EXTRACTION_OK=1"
quit -noprompt
'''
    return script


def extracted_ports(path, top):
    statements = flatten_spice(Path(path).read_text(encoding="utf-8", errors="replace"))
    matches = [s.split()[2:] for s in statements
               if s.lower().startswith('.subckt ') and len(s.split()) >= 2
               and s.split()[1].casefold() == top.casefold()]
    if len(matches) != 1:
        raise ValueError(f"Expected one extracted top .subckt for {top}, found {len(matches)}")
    return matches[0]


def run_checked(args, cwd, outfile, timeout, env=None):
    try:
        completed = subprocess.run(args, cwd=str(cwd), stdout=subprocess.PIPE,
                                   stderr=subprocess.STDOUT, text=True, errors="replace",
                                   timeout=timeout, check=False, env=env)
        log = completed.stdout or ""
        status = completed.returncode
    except subprocess.TimeoutExpired as exc:
        log = (exc.stdout or b"")
        if isinstance(log, bytes):
            log = log.decode("utf-8", errors="replace")
        log += "\nRUNNER_TIMEOUT\n"
        status = 124
    except OSError as exc:
        log = f"RUNNER_EXEC_ERROR: {exc}\n"
        status = 127
    outfile.write_text(log, encoding="utf-8")
    return status, log


def classify_lvs(text, exit_code):
    final = [m.strip() for m in FINAL_LINE.findall(text)]
    if exit_code != 0:
        return "TOOL_ERROR", final
    if not final:
        return "UNPROVEN", final
    # A qualified topology match is NOT a strict LVS match.  Netgen can emit
    # "Netlists match uniquely with property errors" together with a positive
    # "Final result" line.  Reject any qualified match before accepting PASS.
    if MISMATCH.search(text) or re.search(
        r"(?im)^\s*(?:Netlists|Circuits)\s+match uniquely with\b", text
    ):
        return "FAIL", final
    if FINAL_MATCH.search(text) and final and all(re.fullmatch(r"(?:Circuits|Netlists) match uniquely\s*\.", f, re.I) for f in final):
        return "PASS", final
    return "FAIL", final


def resolve_pdk(raw):
    candidates = []
    if raw:
        candidates = [Path(raw).expanduser()]
    else:
        env = os.environ.get("PDK_ROOT", "")
        if env:
            candidates.extend([Path(env), Path(env) / "sky130A"])
        candidates += [Path("/usr/share/pdk/sky130A"), Path("/usr/local/share/pdk/sky130A")]
        candidates += list((Path.home() / ".volare/volare/sky130/versions").glob("*/sky130A"))
    found = sorted(set(p.resolve() for p in candidates if
                       (p / "libs.tech/netgen/sky130A_setup.tcl").is_file() and
                       (p / "libs.tech/magic/sky130A.magicrc").is_file()))
    if not found:
        raise ValueError("SKY130A PDK not found. Supply --pdk /path/to/sky130A")
    if len(found) > 1:
        raise ValueError(f"Multiple PDKs found: {[str(p) for p in found]}. Pin one with --pdk")
    return found[0]


def select_candidates(path, mode, family_filter):
    with path.open(newline="", encoding="utf-8") as stream:
        rows = list(csv.DictReader(stream))
    needed = {"family", "circuit_id", "variant_id", "gds_path", "netlist_path"}
    if not rows or not needed.issubset(rows[0]):
        raise ValueError(f"Missing required columns from {path}: {needed - set(rows[0] if rows else {})}")
    rows = [r for r in rows if not family_filter or r["family"] in family_filter]
    if mode == "all":
        return rows
    selected = {}
    for row in rows:
        family = row["family"]
        if family not in selected or (row["variant_id"] == "variant_0" and
                                     selected[family]["variant_id"] != "variant_0"):
            selected[family] = row
    return [selected[k] for k in sorted(selected)]


def check_outside(out, dataset):
    dataset = dataset.resolve()
    out = out.resolve()
    if out == dataset or dataset in out.parents:
        raise ValueError("--out must be outside the original dataset directory")


def validate_one(row, root, out, pdk, units, width_mode, timeout, dry_run, lvs_only=False,
                 existing_x_units=None, mim_c_units=None, direct=False, layout_top=None):
    family, circuit, variant = row["family"], row["circuit_id"], row["variant_id"]
    case = out / family / circuit / variant
    case.mkdir(parents=True, exist_ok=True)
    report = {"family": family, "circuit_id": circuit, "variant_id": variant,
              "gds": row["gds_path"], "source": row["netlist_path"],
              "drc": "NOT_TESTED", "lvs": "NOT_TESTED", "geometry": "NOT_TESTED",
              "nonmos_audit": "NOT_TESTED", "import": "NOT_TESTED", "golden": False,
              "lvs_verified": False, "port_audit": "NOT_TESTED", "status": "UNPROVEN",
              "mim_pdk_audit": "NOT_APPLICABLE", "resistor_audit": "NOT_TESTED",
              "mos_finger_audit": "UNPROVEN", "issues": []}
    gds, source = Path(row["gds_path"]), Path(row["netlist_path"])
    # Enforce dataset path provenance and expected names: reject swapped references.
    try:
        for p in [gds, source]:
            if not p.is_file():
                raise ValueError(f"Missing source file: {p}")
            if not direct and root.resolve() not in p.resolve().parents:
                raise ValueError(f"Not within --dataset-root: {p}")
        if not direct:
            if gds.parent.parent.name != circuit or gds.parent.name != variant or gds.parent.parent.parent.name != family:
                raise ValueError("GDS family/circuit/variant path doesn't match selection row")
            if source.stem != circuit or source.parent.name != family:
                raise ValueError("SPICE path doesn't match selection row")
        hashes_before = {"gds": file_sha256(gds), "original_spice": file_sha256(source)}
        report["hashes_before"] = hashes_before
        if row.get("variant_sha256") and row["variant_sha256"] != hashes_before["gds"]:
            raise ValueError("GDS SHA-256 differs from selection inventory — possible source changes")
        if layout_top is not None:
            if not IDENT.fullmatch(layout_top):
                raise ValueError("Invalid --layout-top cell name")
            top_candidates = gds_top_cells(gds)
            if layout_top not in top_candidates:
                raise ValueError(f"Requested layout top {layout_top!r} is not a GDS root: {top_candidates}")
            top = layout_top
        else:
            top, top_candidates = choose_top(gds)
        report["layout_top"] = top
        report["top_candidates"] = top_candidates
        adapted = case / "adapter_lvs.spice"
        audit, src_devs = adapt_spice(source, adapted, top, circuit, units, width_mode,
                                       existing_x_units, mim_c_units)
        dump_json(case / "adapter_audit.json", audit)
        report["resistor_audit"] = ("UNPROVEN" if audit["resistor_source_count"]
                                    else "NOT_APPLICABLE")
        if audit["resistor_source_count"]:
            dump_json(case / "resistor_audit.json", {
                "source": audit["resistor_source"], "extracted": "NOT_EXTRACTED_YET",
                "assessment": "Do not equate native resistor, generic LI, and fixed-width precision poly models without PDK and geometry evidence",
            })
        report["adapter_hash"] = file_sha256(adapted)
        report["source_mos_count"] = len(src_devs)
        # Matching total W does not independently verify the finger count.
        # For nf=1 no multi-finger normalization is needed.
        report["mos_finger_audit"] = ("UNPROVEN" if any(
            int(x["source_nf"]) > 1 for x in audit["mos_conversion"])
            else "PASS")
        report["mos_finger_audit_reason"] = (
            "MULTIFINGER_AUDIT_UNIMPLEMENTED" if report["mos_finger_audit"] == "UNPROVEN"
            else "NO_MULTIFINGER_NORMALIZATION_REQUIRED")
        if audit["other_device_lines"] or audit["mim_conversion"]:
            report["nonmos_audit"] = "UNPROVEN"
            report["issues"].append("Non-MOS electrical equivalence/coverage is not fully proven; LVS-only device contract checked separately")
        else:
            report["nonmos_audit"] = "PASS"
        if audit["mim_conversion"]:
            report["mim_pdk_audit"] = "UNPROVEN"
            if not dry_run:
                report["mim_pdk_contracts"] = verify_mim_pdk_contract(
                    pdk, [d["model"] for d in audit["mim_conversion"]])
                report["mim_pdk_audit"] = "PASS"
        report["source_verified"] = True  # only provenance/parse/invariants, NOT electrical equivalence
        if dry_run:
            report["issues"].append("Dry-run: Magic/Netgen have not been executed")
        else:
            extracted = case / "extracted_layout.spice"
            extracted.unlink(missing_ok=True)
            tcl = case / "magic_check.tcl"
            tcl.write_text(make_magic_tcl(gds, top, extracted, audit["original_ports"], lvs_only),
                           encoding="utf-8")
            magic_cmd = ["magic", "-dnull", "-noconsole", "-rcfile",
                         str(pdk / "libs.tech/magic/sky130A.magicrc"), str(tcl)]
            # Magic .magicrc requires PDK_ROOT = parent directory of sky130A.
            magic_env = dict(os.environ, PDK_ROOT=str(pdk.parent))
            magic_rc, magic_log = run_checked(magic_cmd, case, case / "magic.log", timeout,
                                             env=magic_env)
            report["magic_returncode"] = magic_rc
            events = sorted(set(m.group(0) for m in FATAL_MAGIC.finditer(magic_log)))
            report["import_events"] = events
            report["import_event_details"] = import_event_details(magic_log)
            magic_initialized = (magic_rc == 0 and "GOLDEN_TECH=sky130A" in magic_log
                                 and "GOLDEN_LOAD_OK=1" in magic_log
                                 and "GOLDEN_ERROR=" not in magic_log)
            report["import"] = ("UNPROVEN" if not magic_initialized
                                else "REVIEW_REQUIRED" if events else "PASS")
            if events:
                report["issues"].append("Magic import warnings observed; verify no electrically relevant layer ignored")
            if not magic_initialized:
                report["issues"].append("PDK/load not confirmed, inspect magic.log")
                report["lvs"] = ("TOOL_ERROR" if magic_rc != 0 or "GOLDEN_ERROR=" in magic_log
                                 else "UNPROVEN")
            if not lvs_only:
                matched = re.search(r"(?m)^GOLDEN_DRC_TOTAL=(\d+)\s*$", magic_log)
                if not magic_initialized:
                    report["drc"] = "TOOL_ERROR" if magic_rc != 0 or "GOLDEN_ERROR=" in magic_log else "UNPROVEN"
                elif matched:
                    report["drc_count"] = int(matched.group(1))
                    report["drc"] = "PASS" if int(matched.group(1)) == 0 else "FAIL"
                else:
                    report["drc"] = "UNPROVEN"
                    report["issues"].append("DRC count marker missing")
            else:
                report["drc"] = "NOT_TESTED"
                report["geometry"] = "NOT_TESTED"

            extracted_ready = (magic_initialized and "GOLDEN_PORTS_OK=1" in magic_log
                               and "GOLDEN_EXTRACTION_OK=1" in magic_log and extracted.is_file()
                               and extracted.stat().st_size > 0)
            if extracted_ready:
                try:
                    extracted_res = extracted_resistor_models(extracted)
                    report["extracted_resistor_count"] = len(extracted_res)
                    if audit["resistor_source_count"] or extracted_res:
                        report["resistor_audit"] = "UNPROVEN"
                        dump_json(case / "resistor_audit.json", {
                            "source": audit["resistor_source"],
                            "extracted": extracted_res,
                            "assessment": "UNPROVEN: resistor physical class and resistance/geometry equivalence not certified",
                            "source_models": sorted(set(x["model"] for x in audit["resistor_source"])),
                            "extracted_models": sorted(set(x["model"] for x in extracted_res)),
                        })
                        if extracted_res and not audit["resistor_source_count"]:
                            report["issues"].append("Layout resistor(s) present but source resistor audit empty")
                except (OSError, ValueError) as exc:
                    report["resistor_audit"] = "UNPROVEN"
                    report["issues"].append(f"Resistor extraction audit unavailable: {exc}")
                try:
                    report["extracted_top_ports"] = extracted_ports(extracted, top)
                    if report["extracted_top_ports"] != audit["original_ports"]:
                        raise ValueError(f"Extracted top ports {report['extracted_top_ports']} "
                                         f"!= original {audit['original_ports']}")
                    report["port_audit"] = "PASS"
                except (OSError, ValueError) as exc:
                    report["port_audit"] = "FAIL"
                    report["lvs"] = "FAIL"
                    report["issues"].append(f"Port audit failed: {exc}")
                if not lvs_only:
                    try:
                        ext_sig, grouped_count = extracted_geom_signature(extracted)
                        matched_geom, details = compare_geometry(source_geom_signature(src_devs), ext_sig)
                        dump_json(case / "geometry_audit.json", details)
                        report["geometry"] = "PASS" if matched_geom else "FAIL"
                        report["extracted_mos_groups"] = grouped_count
                        if not matched_geom:
                            report["issues"].append("MOS width/length multiset differs; see geometry_audit.json")
                    except (OSError, ValueError) as exc:
                        report["geometry"] = "UNPROVEN"
                        report["issues"].append(f"Geometry audit unavailable: {exc}")
                if report["port_audit"] == "PASS":
                    lvs_output = case / "netgen_comp.out"
                    lvs_output.unlink(missing_ok=True)
                    netgen_cmd = ["netgen", "-batch", "lvs", f"{extracted} {top}",
                                  f"{adapted} {top}",
                                  str(pdk / "libs.tech/netgen/sky130A_setup.tcl"),
                                  str(lvs_output)]
                    ng_rc, ng_log = run_checked(netgen_cmd, case, case / "netgen.stdout.log", timeout)
                    report["netgen_returncode"] = ng_rc
                    if lvs_output.is_file():
                        all_text = ng_log + "\n" + lvs_output.read_text(encoding="utf-8", errors="replace")
                        report["lvs"], report["netgen_final_results"] = classify_lvs(all_text, ng_rc)
                        suspects = []
                        if re.search(r"(?im)^\s*w circuit1:\s", all_text):
                            suspects.append("MOS_WIDTH_OR_OTHER_DEVICE_WIDTH_PROPERTY")
                        if "DEVICE mismatches:" in all_text:
                            mismatch_detail = all_text.split("DEVICE mismatches:", 1)[-1]
                            if re.search(r"(?i)(?:sky130_fd_pr__res_|\bresistor\b)", mismatch_detail):
                                suspects.append("RESISTOR_CLASS_OR_CONNECTIVITY_CANDIDATE")
                        report["suspected_failure_factors"] = suspects
                    else:
                        report["lvs"] = "TOOL_ERROR" if ng_rc != 0 else "UNPROVEN"
                        report["issues"].append("Netgen comparison report missing")
                    if report["lvs"] != "PASS":
                        report["issues"].append("Netgen did not prove an LVS match; inspect netgen logs")
            elif magic_initialized:
                report["issues"].append("Magic port-aware extraction not verified; inspect magic.log")
                report["lvs"] = "TOOL_ERROR" if "GOLDEN_ERROR=" in magic_log else "UNPROVEN"
        hashes_after = {"gds": file_sha256(gds), "original_spice": file_sha256(source)}
        report["hashes_after"] = hashes_after
        if hashes_after != hashes_before:
            report["issues"].append("SOURCE HASH CHANGED during validation: source was modified externally!")
        report["lvs_verified"] = bool(not dry_run and hashes_after == hashes_before and
                                      report["port_audit"] == "PASS" and report["lvs"] == "PASS")
        if lvs_only:
            report["golden"] = False
            if report["lvs_verified"]:
                report["status"] = "LVS_PASS"
            elif report["lvs"] == "TOOL_ERROR":
                report["status"] = "TOOL_ERROR"
            elif report["lvs"] == "FAIL":
                report["status"] = "LVS_FAIL"
            else:
                report["status"] = "UNPROVEN"
        else:
            report["golden"] = bool(report["lvs_verified"] and
                                    report["resistor_audit"] in ("NOT_APPLICABLE", "PASS") and
                                    report["mos_finger_audit"] == "PASS" and
                                    all(report[k] == "PASS" for k in ("drc", "geometry", "import", "nonmos_audit")))
            if report["golden"]:
                report["status"] = "GOLDEN_CANDIDATE"
            elif any(report[k] == "TOOL_ERROR" for k in ("drc", "lvs")):
                report["status"] = "TOOL_ERROR"
            elif any(report[k] == "FAIL" for k in ("drc", "lvs", "geometry")):
                report["status"] = "FAIL"
            else:
                report["status"] = "UNPROVEN"
    except Exception as exc:
        report["issues"].append(f"RUNNER_BLOCKED: {exc}")
        report["status"] = "UNPROVEN"
    dump_json(case / "validation.json", report)
    return report


def main(argv=None):
    ap = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawTextHelpFormatter)
    ap.add_argument("--selected", type=Path, default=None,
                    help="Dataset CSV batch input. Omit when using --gds/--schematic")
    ap.add_argument("--gds", type=Path, help="Direct single-design mode: layout GDS")
    ap.add_argument("--schematic", type=Path, help="Direct single-design mode: one-subckt SPICE source")
    ap.add_argument("--layout-top", help="Optional GDS top root (otherwise infer only if unambiguous)")
    ap.add_argument("--schematic-top", help="Optional original .subckt name (checked against source)")
    ap.add_argument("--dataset-root", type=Path, default=DEFAULT_ROOT)
    ap.add_argument("--out", type=Path, default=Path.home() / "gp/golden_pilot_validation")
    ap.add_argument("--pdk", help="Specific pinned path to sky130A PDK; auto-detect only if unambiguous")
    ap.add_argument("--mode", choices=("pilot", "all"), default="pilot", help="pilot: 1 variant per family (default)")
    ap.add_argument("--family", action="append", help="Filter by family; repeat to choose multiple")
    ap.add_argument("--source-dim-units", choices=("meters", "um"), default=None,
                    help="Required for direct mode; CSV batch defaults to meters")
    ap.add_argument("--width-mode", choices=("per-finger", "total"), default=None,
                    help="Required for direct mode; CSV batch defaults to per-finger")
    ap.add_argument("--existing-x-units", choices=("meters", "um"), default=None,
                    help="Required ONLY when a source contains an existing SKY130 X MOS; "
                         "declare its w/l units explicitly. Omission rejects Existing-X MOS")
    ap.add_argument("--mim-c-units", choices=("meters", "um"), default=None,
                    help="Required if native C devices call sky130_fd_pr__cap_mim_m3_1; "
                         "MIM dimensions must have an evidence-based unit declaration")
    ap.add_argument("--timeout", type=int, default=300, help="Seconds per Magic/Netgen invocation")
    ap.add_argument("--dry-run", action="store_true", help="Inspect/map/convert without launching Magic/Netgen")
    ap.add_argument("--lvs-only", action="store_true", help="Skip DRC/geometry checks; validate LVS only")
    args = ap.parse_args(argv)
    root = args.dataset_root.expanduser().resolve()
    out = args.out.expanduser().resolve()
    direct = args.gds is not None or args.schematic is not None
    if direct and (args.gds is None or args.schematic is None):
        ap.error("Direct mode requires BOTH --gds and --schematic")
    if direct and args.selected is not None:
        ap.error("--selected cannot be combined with --gds/--schematic")
    if direct and (args.family or args.mode != "pilot"):
        ap.error("--family and --mode are for CSV batch mode only")
    if direct and (args.source_dim_units is None or args.width_mode is None):
        ap.error("Direct mode requires explicit --source-dim-units and --width-mode; never guess units or nf semantics")
    if not direct:
        args.source_dim_units = args.source_dim_units or "meters"
        args.width_mode = args.width_mode or "per-finger"
    selected = ((args.selected or (Path.home() / "gp/osiris_selection_report/selected_layouts.csv"))
                .expanduser().resolve())
    if not direct and not selected.is_file():
        ap.error(f"Selected CSV not found: {selected}")
    try:
        if direct:
            gds = args.gds.expanduser().resolve()
            schematic = args.schematic.expanduser().resolve()
            if not gds.is_file() or not schematic.is_file():
                raise ValueError("Both direct GDS and schematic files must exist")
            if out == gds or out == schematic or gds in out.parents or schematic in out.parents:
                raise ValueError("--out overlaps an input file")
            # Avoid writing generated verification evidence inside the source tree.
            if out == gds.parent or out == schematic.parent or gds.parent in out.parents or schematic.parent in out.parents:
                raise ValueError("--out must not be inside the input's source directories")
            statements = flatten_spice(schematic.read_text(encoding="utf-8", errors="replace"))
            subckts = [s.split() for s in statements if s.lower().startswith(".subckt ")]
            if len(subckts) != 1:
                raise ValueError("Direct mode supports exactly one original .subckt")
            circuit_id = subckts[0][1]
            if not IDENT.fullmatch(circuit_id):
                raise ValueError("Unsafe schematic top name")
            if args.schematic_top and args.schematic_top.lower() != circuit_id.lower():
                raise ValueError(f"--schematic-top {args.schematic_top} differs from original {circuit_id}")
            rows = [{"family": "direct", "circuit_id": circuit_id,
                     "variant_id": "input", "gds_path": str(gds),
                     "netlist_path": str(schematic)}]
        else:
            check_outside(out, root)
            rows = select_candidates(selected, args.mode, set(args.family or []))
        if not rows:
            raise ValueError("No selected rows match the requested pilot/family filters")
        pdk = None if args.dry_run else resolve_pdk(args.pdk)
        if not args.dry_run and (not shutil.which("magic") or not shutil.which("netgen")):
            raise ValueError("Magic and Netgen executables must both be available in PATH")
    except ValueError as exc:
        ap.error(str(exc))
    out.mkdir(parents=True, exist_ok=True)
    environment = {"dataset_root": ("NOT_APPLICABLE" if direct else str(root)), "selection_csv": ("DIRECT_INPUT" if direct else str(selected)),
                   "selection_sha256": ("NOT_APPLICABLE" if direct else file_sha256(selected)), "pdk": str(pdk) if pdk else "DRY_RUN",
                   "mode": args.mode, "source_dim_units": args.source_dim_units,
                   "existing_x_units": args.existing_x_units or "REJECT_IF_PRESENT",
                   "mim_c_units": args.mim_c_units or "REJECT_IF_PRESENT",
                   "supported_mim_c_models": [MIM_C_MODEL],
                   "width_mode": args.width_mode, "timeout_seconds": args.timeout,
                   "validation_scope": "LVS_ONLY" if args.lvs_only else "FULL",
                   "dataset_role": "DIRECT_INPUT" if direct else "DEVELOPMENT_NOT_FINAL_BENCHMARK",
                   "input_mode": "direct" if direct else "osiris_csv",
                   "resistor_policy": "AUDIT_ONLY_NO_MODEL_RELABELLING",
                   "validator_sha256": file_sha256(Path(__file__))}
    if pdk:
        environment.update({"pdk_setup_sha256": file_sha256(pdk / "libs.tech/netgen/sky130A_setup.tcl"),
                            "magicrc_sha256": file_sha256(pdk / "libs.tech/magic/sky130A.magicrc")})
    dump_json(out / "environment_lock.json", environment)
    results = []
    for idx, row in enumerate(rows, start=1):
        print(f"[{idx}/{len(rows)}] {row['family']} / {row['circuit_id']} / {row['variant_id']}", flush=True)
        result = validate_one(row, root, out, pdk, args.source_dim_units,
                              args.width_mode, args.timeout, args.dry_run,
                              args.lvs_only, args.existing_x_units, args.mim_c_units,
                              direct=direct, layout_top=args.layout_top)
        print(f"    {result['status']} | DRC={result['drc']} LVS={result['lvs']} "
              f"geometry={result['geometry']} nonmos={result['nonmos_audit']} import={result['import']}", flush=True)
        results.append(result)
    fields = ["family", "circuit_id", "variant_id", "status", "golden", "lvs_verified", "port_audit",
              "drc", "lvs", "geometry", "nonmos_audit", "resistor_audit", "mos_finger_audit", "import", "drc_count", "layout_top", "gds", "source", "issues"]
    with (out / "results.csv").open("w", newline="", encoding="utf-8") as fd:
        w = csv.DictWriter(fd, fieldnames=fields, extrasaction="ignore")
        w.writeheader()
        for result in results:
            w.writerow({**result, "issues": " | ".join(result.get("issues", []))})
    summary = {"cases": len(results), "status_counts": dict(Counter(r["status"] for r in results)),
               "drc": dict(Counter(r["drc"] for r in results)),
               "lvs": dict(Counter(r["lvs"] for r in results)),
               "geometry": dict(Counter(r["geometry"] for r in results)),
               "nonmos_audit": dict(Counter(r["nonmos_audit"] for r in results)),
               "resistor_audit": dict(Counter(r["resistor_audit"] for r in results)),
               "mos_finger_audit": dict(Counter(r["mos_finger_audit"] for r in results)),
               "import": dict(Counter(r["import"] for r in results)),
               "golden_candidates": sum(r["golden"] for r in results),
               "lvs_verified": sum(r["lvs_verified"] for r in results),
               "validation_scope": "LVS_ONLY" if args.lvs_only else "FULL",
               "note": "GOLDEN_CANDIDATE is automated screening, not final independently approved GOLDEN"}
    dump_json(out / "summary.json", summary)
    print("\nOutput:", out, "\n", json.dumps(summary, indent=2, ensure_ascii=False))
    return 0 if all(r["lvs_verified"] if args.lvs_only else r["golden"] for r in results) else 2

if __name__ == "__main__":
    sys.exit(main())

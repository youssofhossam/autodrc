#!/usr/bin/env python3
"""SPICE Adapter v0 (standalone implementation).

Only SPICE source device normalization; no Magic/Netgen execution or LVS verdict.
Resistor electrical equivalence is NOT qualified. Non-MOS unsupported devices
are recorded as UNPROVEN, not silently certified. Preserve source files.
"""
# Dataset provenance and compatibility notes: see ../DATA_SOURCES.md.
from __future__ import annotations

import argparse
import hashlib
import json
import re
import sys
from decimal import Decimal, InvalidOperation
from pathlib import Path

VALID_MOS = re.compile(r"^sky130_fd_pr__(?:n|p)fet_[A-Za-z0-9_]+$", re.I)
IDENT = re.compile(r"^[A-Za-z_][A-Za-z0-9_$.-]*$")
MAGIC_EXTRACTED_NODE = re.compile(r"^[A-Za-z_][A-Za-z0-9_$./#-]*$")
PROPERTY = re.compile(r"^([A-Za-z][A-Za-z0-9_]*)=(.+)$")
MIM_C_MODEL = "sky130_fd_pr__cap_mim_m3_1"
MIM_C_PREFIX = "sky130_fd_pr__cap_mim"
RES_MODEL = re.compile(r"^sky130_fd_pr__res_[A-Za-z0-9_]+$", re.I)
ENGINEERING_SUFFIX = {"t": "1e12", "g": "1e9", "meg": "1e6", "k": "1e3",
                      "m": "1e-3", "u": "1e-6", "n": "1e-9", "p": "1e-12", "f": "1e-15"}

def file_sha256(path):
    dig = hashlib.sha256()
    with Path(path).open("rb") as f:
        for b in iter(lambda: f.read(1024 * 1024), b""):
            dig.update(b)
    return dig.hexdigest()


def flatten_spice(text):
    lines = []
    for raw in text.splitlines():
        s = raw.strip()
        if not s or s.startswith("*"):
            if not s:
                lines.append("")
            continue
        if s.startswith("+"):
            if not lines or not lines[-1]:
                raise ValueError("SPICE continuation line without preceding statement")
            lines[-1] += " " + s[1:].strip()
        else:
            lines.append(s)
    return lines


def parse_numeric(token):
    # Deliberately reject expressions/engineering suffixes until verified.
    try:
        value = Decimal(token)
    except InvalidOperation as exc:
        raise ValueError(f"Unsupported numeric expression '{token}'") from exc
    if not value.is_finite() or value <= 0:
        raise ValueError(f"Invalid nonpositive physical dimension '{token}'")
    return value


def format_decimal(num):
    return format(num.normalize(), 'f')


def size_um(token, units):
    return parse_numeric(token) * (Decimal("1000000") if units == "meters" else Decimal(1))


def adapt_mim_c(tokens, mim_units, occupied_names):
    """Convert a native C call to the supported SKY130 two-pin MIM subcircuit.

    This preserves terminal order (source convention: first node = c0/top,
    second node = c1/bottom).  It does NOT prove that the source author used
    that convention; user must validate it against an independent reference.
    """
    name = tokens[0]
    if len(tokens) < 4 or tokens[3].lower() != MIM_C_MODEL:
        raise ValueError(f"Unsupported SKY130 MIM model/format on {name}: {' '.join(tokens)}")
    if mim_units not in ("meters", "um"):
        raise ValueError(f"MIM {name} requires --mim-c-units meters or --mim-c-units um")
    if len(tokens) != 6:
        raise ValueError(f"MIM {name}: expected exactly two pins, model, w and l")
    if not all(IDENT.fullmatch(pin) or pin == "0" for pin in tokens[1:3]):
        raise ValueError(f"MIM {name}: invalid pin syntax")
    props = {}
    for token in tokens[4:]:
        match = PROPERTY.fullmatch(token)
        if match is None:
            raise ValueError(f"MIM {name}: unsupported property syntax: {token}")
        key, val = match.group(1).lower(), match.group(2)
        if key in props:
            raise ValueError(f"MIM {name}: duplicate parameter {key}")
        props[key] = val
    if set(props) != {"w", "l"}:
        raise ValueError(f"MIM {name}: require only numeric w/l, got {sorted(props)}")
    w_um = size_um(props["w"], mim_units)
    l_um = size_um(props["l"], mim_units)
    new_name = "XCAP" + name[1:]
    if new_name.casefold() in occupied_names:
        raise ValueError(f"MIM {name}: converted instance name collides with existing {new_name}")
    occupied_names.add(new_name.casefold())
    converted = " ".join([new_name, tokens[1], tokens[2], tokens[3],
                          "w=" + format_decimal(w_um),
                          "l=" + format_decimal(l_um)])
    return converted, {
        "source": " ".join(tokens), "converted": converted,
        "source_instance": name, "lvs_instance": new_name,
        "model": tokens[3], "original_terminals": tokens[1:3],
        "lvs_terminals": tokens[1:3], "source_units": mim_units,
        "source_w": props["w"], "source_l": props["l"],
        "lvs_w_um": format_decimal(w_um), "lvs_l_um": format_decimal(l_um),
        "electrical_equivalence": "NOT_CLAIMED",
        "pin_order_contract": "source node1=c0/top, node2=c1/bottom; needs independent confirmation",
    }


def verify_mim_pdk_contract(pdk, models):
    """Check local pinned PDK declares the specific two-terminal model + w/l.

    Not a full SPICE behavior equivalence check; only a contract gate.
    """
    results = []
    root = Path(pdk) / "libs.ref/sky130_fd_pr"
    setup = Path(pdk) / "libs.tech/netgen/sky130A_setup.tcl"
    if not setup.is_file():
        raise ValueError("MIM PDK: Netgen setup file missing")
    setup_text = setup.read_text(encoding="utf-8", errors="replace")
    for model in sorted(set(models)):
        if model.lower() != MIM_C_MODEL:
            raise ValueError(f"MIM PDK: unsupported model {model}")
        if not re.search(r"(?im)^\s*lappend\s+devices\s+" + re.escape(model) + r"\s*(?:#.*)?$", setup_text):
            raise ValueError(f"MIM PDK: model {model} absent from pinned Netgen device declarations")
        candidates = []
        for directory in (root / "spice", root / "cells/cap_mim_m3"):
            if directory.is_dir():
                candidates.extend(sorted(directory.glob(model + "*.spice")))
        found = None
        for path in candidates:
            stmts = flatten_spice(path.read_text(encoding="utf-8", errors="replace"))
            for stmt in stmts:
                parts = stmt.split()
                if len(parts) >= 4 and parts[0].lower() == ".subckt" and parts[1].lower() == model:
                    pins = [x for x in parts[2:] if "=" not in x]
                    props = {x.split("=", 1)[0].lower() for x in parts[2:] if "=" in x}
                    if len(pins) != 2 or tuple(p.lower() for p in pins) != ("c0", "c1"):
                        raise ValueError(f"MIM PDK: unexpected pins {pins} in {path}")
                    if not {"w", "l"}.issubset(props):
                        raise ValueError(f"MIM PDK: expected w/l parameters in {path}")
                    found = {"model": model, "pdk_file": str(path),
                             "pdk_sha256": file_sha256(path), "subckt_pins": pins,
                             "w_l_supported": True, "setup_sha256": file_sha256(setup)}
                    break
            if found:
                break
        if found is None:
            raise ValueError(f"MIM PDK: missing validated two-pin .subckt for {model}")
        results.append(found)
    return results


def spice_resistance_ohms(raw):
    """Read single decimal literal with SPICE engineering suffix; never evaluate expressions."""
    m = re.fullmatch(r"([0-9]+(?:\.[0-9]*)?|\.[0-9]+)([eE][+-]?[0-9]+)?([A-Za-z]*)", raw.strip())
    if not m:
        raise ValueError(f"Unsupported resistor value: {raw!r}")
    base = Decimal(m.group(1) + (m.group(2) or ""))
    suffix = m.group(3).lower()
    if suffix not in ENGINEERING_SUFFIX and suffix != "":
        raise ValueError(f"Unsupported resistance suffix in {raw!r}")
    r = base * Decimal(ENGINEERING_SUFFIX.get(suffix, "1"))
    if not r.is_finite() or r <= 0:
        raise ValueError(f"Invalid nonpositive resistance {raw!r}")
    return format_decimal(r)


def resistor_inventory(statements, *, extracted=False):
    """Evidence only: parse resistor records without changing models/ports.

    Extracted Magic node names use a separate syntax (including '#' and '/');
    source-schematic nodes retain the stricter original identifier contract.
    """
    node_pattern = MAGIC_EXTRACTED_NODE if extracted else IDENT
    results = []
    for statement in statements:
        t = statement.split()
        if not t or t[0].startswith("."):
            continue
        kind = t[0][0].upper()
        is_r = kind == "R"
        model_pos = -1
        if kind == "X":
            model_pos = next((i for i in range(1, len(t)) if RES_MODEL.fullmatch(t[i])), -1)
        if not is_r and model_pos < 0:
            continue
        if is_r:
            if len(t) < 4:
                raise ValueError(f"Incomplete resistor declaration: {statement}")
            # Native R format can be R nodeA nodeB <value> or R nodeA nodeB model r=<value>.
            model = t[3] if len(t) >= 5 else "<SPICE_PRIMITIVE>"
            nodes = t[1:3]
            extras = t[4:] if len(t) >= 5 else [t[3]]
        else:
            model = t[model_pos]
            nodes = t[1:model_pos]
            extras = t[model_pos+1:]
        if not nodes or any(not node_pattern.fullmatch(x) and x != "0" for x in nodes):
            raise ValueError(f"Invalid resistor node declaration on {t[0]}")
        prop = {}
        for item in extras:
            m = PROPERTY.fullmatch(item)
            if m:
                key = m.group(1).lower()
                if key in prop:
                    raise ValueError(f"Duplicate resistor parameter {key} on {t[0]}")
                prop[key] = m.group(2)
        r_ohms = None
        if "r" in prop:
            r_ohms = spice_resistance_ohms(prop["r"])
        elif is_r and len(t) == 4:
            r_ohms = spice_resistance_ohms(t[3])
        results.append({"instance": t[0], "instance_type": kind, "nodes": nodes,
                        "model": model, "resistance_ohms_if_literal": r_ohms,
                        "source_line": statement,
                        "contract": "UNPROVEN_MODEL_AND_LAYOUT_PROPERTY_EQUIVALENCE"})
    return results


def normalized_mos_signature(tokens, units, width_mode):
    if len(tokens) < 8:
        raise ValueError(f"Missing properties on MOS: {tokens[0]}")
    props = {}
    for t in tokens[6:]:
        m = PROPERTY.fullmatch(t)
        if not m:
            raise ValueError(f"Unsupported parameter syntax '{t}' on {tokens[0]}")
        key, val = m.group(1).lower(), m.group(2)
        if key in props:
            raise ValueError(f"Duplicate {key} in {tokens[0]}")
        props[key] = val
    if not set(["w", "l"]).issubset(props):
        raise ValueError(f"Missing w/l on {tokens[0]}")
    # Fail closed rather than silently dropping device properties.
    unknown = set(props) - {"w", "l", "nf"}
    if unknown:
        raise ValueError(f"Unsupported model parameters for {tokens[0]}: {sorted(unknown)}")
    nf_raw = props.get("nf", "1")
    try:
        nf = int(nf_raw)
    except ValueError as exc:
        raise ValueError(f"Non-integer nf on {tokens[0]}") from exc
    if nf < 1 or str(nf) != nf_raw:
        raise ValueError(f"Invalid nf on {tokens[0]}: {nf_raw}")
    w_um = size_um(props["w"], units)
    l_um = size_um(props["l"], units)
    # Dataset per-finger interpretation: aggregate the extracted physical fingers.
    # If the original W already means total width, use --width-mode total instead.
    eff_w = w_um * (nf if width_mode == "per-finger" else 1)
    return eff_w, l_um, nf


def adapt_spice(source: Path, out: Path, top: str, circuit: str, units: str,
                 width_mode: str, existing_x_units=None, mim_c_units=None):
    statements = flatten_spice(source.read_text(encoding="utf-8", errors="replace"))
    subckts = [s for s in statements if s.lower().startswith(".subckt ")]
    ends = [s for s in statements if s.lower().startswith(".ends")]
    if len(subckts) != 1 or len(ends) != 1:
        raise ValueError("Expected exactly one .subckt and one .ends (no hierarchy supported yet)")
    sub_tokens = subckts[0].split()
    if len(sub_tokens) < 3 or sub_tokens[1].lower() != circuit.lower():
        raise ValueError(f"Source .subckt '{sub_tokens[1] if len(sub_tokens)>1 else ''}' does not match circuit ID '{circuit}'")
    if not IDENT.fullmatch(top):
        raise ValueError(f"Unsafe GDS top-cell name: {top!r}")
    expected = []
    transformed = []
    audit = []
    other_devices = []
    mim_conversions = []
    resistor_records = resistor_inventory(statements)
    # Prevent an accidental duplicate XCAP name from changing the netlist.
    source_instance_names = {x.split()[0].casefold() for x in statements
                             if x and x[0].upper() in ("X", "M", "R", "C")
                             and not x.startswith(".")}
    for s in statements:
        if not s:
            transformed.append("")
            continue
        tokens = s.split()
        token0 = tokens[0].lower()
        if token0 == ".subckt":
            transformed.append(" ".join([".subckt", top] + sub_tokens[2:]))
        elif token0 == ".ends":
            if len(tokens) > 1 and tokens[1].lower() != circuit.lower():
                raise ValueError(f"Unexpected .ends name: {tokens[1]}")
            transformed.append(".ends " + top)
        elif s[0].upper() == "M" and not s.startswith("."):
            if len(tokens) < 8 or not VALID_MOS.fullmatch(tokens[5]):
                raise ValueError(f"Unsupported MOS model or format: {s}")
            w, l, nf = normalized_mos_signature(tokens, units, width_mode)
            converted = " ".join(["X" + tokens[0][1:]] + tokens[1:6] +
                                 ["w=" + format_decimal(w), "l=" + format_decimal(l)])
            # Four electrical terminals preserved in exactly the same order.
            assert converted.split()[1:5] == tokens[1:5]
            transformed.append(converted)
            expected.append({"model": tokens[5].lower(), "w": w, "l": l,
                             "name": tokens[0], "terminals": tokens[1:5]})
            audit.append({"source": s, "converted": converted,
                          "terminals_unchanged": True, "source_nf": nf,
                          "source_units": units, "width_mode": width_mode})
        elif s[0].upper() == "C" and any(MIM_C_PREFIX in part.lower() for part in tokens[1:]):
            # Fail closed if a SKY130 MIM reference is malformed or misplaced.
            # adapt_mim_c enforces the model at the correct token position.
            converted, cap_audit = adapt_mim_c(tokens, mim_c_units, source_instance_names)
            transformed.append(converted)
            mim_conversions.append(cap_audit)
        elif token0.startswith("."):
            if token0 not in (".param", ".include", ".global", ".model", ".lib"):
                raise ValueError(f"Unsupported SPICE directive: {tokens[0]}")
            # External includes/params need separate controlled evaluation, not guessing.
            raise ValueError(f"Directive {tokens[0]} requires explicit reference resolution")
        else:
            # P1: Existing SKY130 MOS subcircuit calls must be normalized in the
            # emitted LVS netlist, not just in expected/audit data.  An X instance
            # can have different unit conventions from an M instance, so units
            # are a REQUIRED explicit declaration whenever an Existing-X MOS
            # appears.  Never guess units from the magnitude of w/l.
            if s[0].upper() not in ("X", "R", "C"):
                raise ValueError(f"Unsupported SPICE instance type: {s}")
            is_x_mos = (s[0].upper() == "X" and len(tokens) >= 6
                        and VALID_MOS.fullmatch(tokens[5]))
            if is_x_mos:
                if existing_x_units not in ("meters", "um"):
                    raise ValueError(
                        f"Existing-X MOS {tokens[0]}: dimensional units are ambiguous; "
                        "declare --existing-x-units meters or --existing-x-units um"
                    )
                w, l, nf = normalized_mos_signature(tokens, existing_x_units, width_mode)
                if nf != 1:
                    raise ValueError(
                        f"Existing-X MOS {tokens[0]} with nf={nf} is unsupported; "
                        "do not silently reinterpret finger multiplicity"
                    )
                converted = " ".join(tokens[:6] +
                                     ["w=" + format_decimal(w), "l=" + format_decimal(l)])
                if converted.split()[1:6] != tokens[1:6]:
                    raise ValueError(f"Existing-X MOS {tokens[0]} terminals/model changed")
                transformed.append(converted)
                expected.append({"model": tokens[5].lower(), "w": w, "l": l,
                                 "name": tokens[0], "terminals": tokens[1:5]})
                audit.append({"source": s, "converted": converted,
                              "instance_type": "X", "terminals_unchanged": True,
                              "source_nf": nf, "source_units": existing_x_units,
                              "width_mode": width_mode})
            else:
                # Other non-MOS X/R/C remain untouched and UNPROVEN. P2 only
                # supports native C calls to the whitelisted MIM m3_1 model.
                transformed.append(s)
                other_devices.append(s)
    if not expected:
        raise ValueError("No recognized MOS in original schematic; cannot audit geometry")
    out.write_text("* LVS adapter - DO NOT USE AS GOLDEN SOURCE\n" + "\n".join(transformed) + "\n", encoding="utf-8")
    return {"original_subckt": sub_tokens[1], "layout_top": top,
            "original_ports": sub_tokens[2:], "mos_count": len(expected),
            "mos_conversion": audit, "other_device_lines": other_devices,
            "mim_conversion": mim_conversions, "mim_source_count": len(mim_conversions),
            "resistor_source_count": len(resistor_records), "resistor_source": resistor_records,
            "source_mos": [{**d, "w": str(d["w"]), "l": str(d["l"])} for d in expected]}, expected




# Compatibility alias for existing validator versions and external callers.
adapt_osiris = adapt_spice

def main(argv=None):
    parser = argparse.ArgumentParser(description="Standalone SPICE device adapter")
    parser.add_argument("--schematic", required=True, type=Path)
    parser.add_argument("--out", required=True, type=Path)
    parser.add_argument("--layout-top", required=True)
    parser.add_argument("--schematic-top", required=True)
    parser.add_argument("--source-dim-units", required=True, choices=("meters", "um"))
    parser.add_argument("--width-mode", required=True, choices=("per-finger", "total"))
    parser.add_argument("--existing-x-units", choices=("meters", "um"))
    parser.add_argument("--mim-c-units", choices=("meters", "um"))
    args = parser.parse_args(argv)
    source = args.schematic.expanduser().resolve()
    target = args.out.expanduser().resolve()
    if not source.is_file():
        parser.error(f"Missing source SPICE: {source}")
    if source == target:
        parser.error("Output must differ from original source")
    if target.exists() or target.is_symlink():
        parser.error(f"Refusing to overwrite existing output: {target}")
    # Require scratch destination outside the source directory/tree.
    if target.parent == source.parent or source.parent in target.parents:
        parser.error("Output must be outside schematic source directory")
    target.parent.mkdir(parents=True, exist_ok=True)
    import tempfile, os
    fd, temp = tempfile.mkstemp(prefix=".adapter_spice_", dir=target.parent)
    os.close(fd)
    staged = Path(temp)
    before = file_sha256(source)
    try:
        audit, expected = adapt_spice(source, staged, args.layout_top,
                                       args.schematic_top, args.source_dim_units,
                                       args.width_mode, args.existing_x_units,
                                       args.mim_c_units)
        if not staged.is_file() or staged.stat().st_size == 0:
            raise RuntimeError("Adapter output was not created")
        # Fail closed on observed source mutation (source could change concurrently).
        if file_sha256(source) != before:
            raise RuntimeError("Source changed during conversion")
        staged.replace(target)
    finally:
        staged.unlink(missing_ok=True)
    print(json.dumps({"adapter": "v0", "output": str(target),
                      "mos_count": len(expected),
                      "mim_count": audit["mim_source_count"],
                      "resistor_count": audit["resistor_source_count"],
                      "nonmos_unproven": bool(audit["other_device_lines"]),
                      "resistor_equivalence": "UNPROVEN" if audit["resistor_source_count"] else "NOT_APPLICABLE",
                      "source_sha256": file_sha256(source)}, indent=2))
    return 0


if __name__ == "__main__":
    sys.exit(main())

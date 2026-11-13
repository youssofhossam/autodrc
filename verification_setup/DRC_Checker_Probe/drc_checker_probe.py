#!/usr/bin/env python3
"""Independent, non-mutating Magic DRC evidence probe for a validator run.

Reads baseline validation metadata, invokes Magic in isolated scratch directories,
compares its DRC counts to the baseline, and captures violation classes. The input
GDS/SPICE, baseline reports, and PDK are never modified. Does not certify signoff.
"""
# Dataset provenance and compatibility notes: see ../DATA_SOURCES.md.
from __future__ import annotations

import argparse
import hashlib
import json
import os
import re
import subprocess
import sys
from pathlib import Path


def sha256(path: Path) -> str:
    h = hashlib.sha256()
    with path.open('rb') as f:
        for chunk in iter(lambda: f.read(1024 * 1024), b''):
            h.update(chunk)
    return h.hexdigest()


def quote_tcl(value: object) -> str:
    s = str(value)
    return '"' + s.replace('\\', '\\\\').replace('"', '\\"').replace('$', '\\$').replace('[', '\\[').replace(']', '\\]') + '"'


def build_tcl(gds: Path, top: str, *, original_import: bool) -> str:
    # Identical verification style/check/catchup as the baseline validator. Only import settings vary.
    parts = [
        '# Read-only Magic DRC probe. No layout saves or extraction outputs.',
        'if {[catch {gds readonly true} e]} {puts "PROBE_ERROR=READONLY:$e"; exit 20}',
    ]
    if original_import:
        parts.extend([
            'if {[catch {gds flatten true} e]} {puts "PROBE_ERROR=FLATTEN:$e"; exit 21}',
            'if {[catch {gds rescale true} e]} {puts "PROBE_ERROR=RESCALE:$e"; exit 22}',
            'if {[catch {tech unlock *} e]} {puts "PROBE_ERROR=TECH_UNLOCK:$e"; exit 23}',
            'if {[catch {cif istyle sky130(vendor)} e]} {puts "PROBE_ERROR=CIF_STYLE:$e"; exit 24}',
        ])
    parts.extend([
        f'if {{[catch {{gds read {quote_tcl(gds)}}} e]}} {{puts "PROBE_ERROR=GDS_READ:$e"; exit 25}}',
        f'if {{[catch {{load {quote_tcl(top)}}} e]}} {{puts "PROBE_ERROR=LOAD:$e"; exit 26}}',
        'puts "PROBE_TECH=[tech name]"',
        'if {[tech name] ne "sky130A"} {puts "PROBE_ERROR=WRONG_TECH"; exit 27}',
        'if {[catch {drc style drc(full)} e]} {puts "PROBE_ERROR=STYLE:$e"; exit 28}',
        'puts "PROBE_STYLE=[drc style]"',
        'if {[catch {select top cell} e]} {puts "PROBE_ERROR=SELECT:$e"; exit 29}',
        'if {[catch {snap internal} e]} {puts "PROBE_ERROR=SNAP:$e"; exit 29}',
        'if {[catch {select bbox} bb]} {puts "PROBE_ERROR=BBOX:$bb"; exit 29}',
        'if {[llength $bb] != 4 || [lindex $bb 0] >= [lindex $bb 2] || [lindex $bb 1] >= [lindex $bb 3]} {puts "PROBE_ERROR=EMPTY_BBOX:$bb"; exit 29}',
        'puts "PROBE_BBOX=$bb"',
        'if {[catch {box values {*}$bb} e]} {puts "PROBE_ERROR=BOX:$e"; exit 29}',
        'if {[catch {drc check} e]} {puts "PROBE_ERROR=DRC_CHECK:$e"; exit 30}',
        'if {[catch {drc catchup} e]} {puts "PROBE_ERROR=DRC_CATCHUP:$e"; exit 31}',
        'if {[catch {drc listall count total} n]} {puts "PROBE_ERROR=DRC_TOTAL:$e"; exit 32}',
        'puts "PROBE_TOTAL=$n"',
        'if {[catch {drc list count total} nlocal]} {puts "PROBE_ERROR=DRC_LOCAL:$e"; exit 33}',
        'puts "PROBE_LOCAL_COUNT=$nlocal"',
        'if {[catch {drc listall why} all]} {puts "PROBE_ERROR=DRC_RULES:$e"; exit 34}',
        'puts "PROBE_RULES_BEGIN"',
        'foreach {desc regions} $all {',
        '    set clean [string map [list "\\n" " " "\\r" " " "\\t" " "] $desc]',
        '    puts "PROBE_RULE=$clean\\tCOUNT=[llength $regions]"',
        '}',
        'puts "PROBE_RULES_END"',
        'puts "PROBE_COMPLETE=1"',
        'quit -noprompt',
    ])
    return '\n'.join(parts) + '\n'


def choose_cases(root: Path, case_paths: list[str], max_cases: int) -> list[tuple[Path, dict]]:
    if case_paths:
        candidates = [root / p / 'validation.json' for p in case_paths]
    else:
        candidates = sorted(root.glob('*/*/*/validation.json'))
    found = []
    for path in candidates:
        if not path.is_file():
            raise ValueError(f'Missing validation.json: {path}')
        data = json.loads(path.read_text(encoding='utf-8'))
        if data.get('drc') not in ('FAIL', 'PASS'):
            continue
        found.append((path, data))
    if case_paths:
        return found
    families: dict[str, list[tuple[Path, dict]]] = {}
    for item in found:
        families.setdefault(str(item[1].get('family', 'UNKNOWN')), []).append(item)
    selected = []
    for _, family_cases in sorted(families.items()):
        family_cases.sort(key=lambda x: (x[1].get('geometry') != 'PASS', int(x[1].get('drc_count', 10**9)), str(x[0])))
        selected.append(family_cases[0])
    return selected[:max_cases]


def parse_log(log: str, rc: int) -> dict:
    def val(key: str) -> str | None:
        m = re.search(r'(?m)^' + re.escape(key) + r'=(.*)$', log)
        return m.group(1).strip() if m else None
    errors = re.findall(r'(?m)^PROBE_ERROR=(.*)$', log)
    rules = []
    for line in log.splitlines():
        if line.startswith('PROBE_RULE=') and '\tCOUNT=' in line:
            name, num = line[len('PROBE_RULE='):].rsplit('\tCOUNT=', 1)
            try:
                rules.append({'description': name, 'locations': int(num)})
            except ValueError:
                errors.append('Malformed rule count: ' + line)
    total = val('PROBE_TOTAL')
    parsed_total = int(total) if total is not None and total.isdigit() else None
    return {
        'returncode': rc, 'complete': val('PROBE_COMPLETE') == '1', 'errors': errors,
        'tech': val('PROBE_TECH'), 'style': val('PROBE_STYLE'), 'bbox': val('PROBE_BBOX'),
        'total': parsed_total, 'local_count': val('PROBE_LOCAL_COUNT'),
        'rule_classes': rules,
        'valid': rc == 0 and val('PROBE_COMPLETE') == '1' and not errors and parsed_total is not None,
    }


def main() -> int:
    ap = argparse.ArgumentParser(description=__doc__)
    ap.add_argument('--root', required=True, type=Path, help='Full validation output folder')
    ap.add_argument('--pdk', required=True, type=Path, help='Pinned sky130A directory')
    ap.add_argument('--out', required=True, type=Path, help='New, separate diagnostics output folder')
    ap.add_argument('--case', action='append', default=[], help='Relative family/circuit/variant path; repeatable')
    ap.add_argument('--max-cases', type=int, default=2)
    ap.add_argument('--timeout', type=int, default=150)
    ap.add_argument('--compare-original-import', action='store_true', help='Also try original dataset import options in separate Magic session')
    args = ap.parse_args()
    root, pdk, out = [p.expanduser().resolve() for p in (args.root, args.pdk, args.out)]
    if not root.is_dir():
        ap.error('Baseline output directory not found')
    if not (pdk / 'libs.tech/magic/sky130A.magicrc').is_file():
        ap.error('SKY130A Magic rcfile not found')
    if out == root or root in out.parents:
        ap.error('Output must be outside baseline results directory')
    if out == pdk or pdk in out.parents:
        ap.error('Output must be outside PDK directory')
    if args.max_cases < 1 or args.timeout < 1:
        ap.error('--max-cases and --timeout must be positive')
    try:
        cases = choose_cases(root, args.case, args.max_cases)
    except (ValueError, OSError, json.JSONDecodeError) as exc:
        ap.error(str(exc))
    if not cases:
        ap.error('No cases with a recorded DRC PASS or FAIL found')
    out.mkdir(parents=True, exist_ok=True)
    all_results = []
    for validation_path, data in cases:
        rel = validation_path.parent.relative_to(root)
        gds = Path(str(data.get('gds', ''))).expanduser().resolve()
        src = Path(str(data.get('source', ''))).expanduser().resolve()
        top = str(data.get('layout_top', ''))
        if not gds.is_file() or not src.is_file() or not top:
            all_results.append({'case': str(rel), 'error': 'Missing GDS/SPICE or top cell in baseline record'})
            continue
        original_hashes = {'gds': sha256(gds), 'original_spice': sha256(src)}
        result = {'case': str(rel), 'baseline_v6': {'drc': data.get('drc'), 'count': data.get('drc_count'), 'lvs': data.get('lvs')}, 'modes': {}, 'source_hashes_before': original_hashes}
        for label, orig in [('same_import', False)] + ([('original_import_options', True)] if args.compare_original_import else []):
            scratch = out / rel / label
            scratch.mkdir(parents=True, exist_ok=True)
            tcl = scratch / 'drc_probe.tcl'
            log_path = scratch / 'magic_probe.log'
            tcl.write_text(build_tcl(gds, top, original_import=orig), encoding='utf-8')
            env = dict(os.environ, PDK_ROOT=str(pdk.parent))
            cmd = ['magic', '-dnull', '-noconsole', '-rcfile', str(pdk / 'libs.tech/magic/sky130A.magicrc'), str(tcl)]
            try:
                proc = subprocess.run(cmd, cwd=scratch, env=env, stdout=subprocess.PIPE, stderr=subprocess.STDOUT, text=True, errors='replace', timeout=args.timeout)
                raw, rc = proc.stdout, proc.returncode
            except subprocess.TimeoutExpired as exc:
                raw, rc = (exc.stdout or b'').decode(errors='replace') if isinstance(exc.stdout, bytes) else (exc.stdout or ''), -99
                raw += '\nPROBE_ERROR=TIMEOUT'
            except OSError as exc:
                raw, rc = f'PROBE_ERROR=TOOL_START:{exc}', -98
            log_path.write_text(raw, encoding='utf-8')
            parsed = parse_log(raw, rc)
            if label == 'same_import':
                parsed['agrees_with_v6_total'] = parsed['valid'] and parsed['total'] == data.get('drc_count')
            result['modes'][label] = parsed
        result['source_hashes_after'] = {'gds': sha256(gds), 'original_spice': sha256(src)}
        result['source_unchanged'] = result['source_hashes_before'] == result['source_hashes_after']
        all_results.append(result)
    output = {'scope': 'DRC diagnostic only; not signoff certification or a change to the baseline', 'cases': all_results}
    target = out / 'drc_probe_summary.json'
    target.write_text(json.dumps(output, indent=2, ensure_ascii=False) + '\n', encoding='utf-8')
    print('DRC PROBE REPORT:', target)
    for result in all_results:
        if 'error' in result:
            print(result['case'], 'ERROR:', result['error'])
            continue
        p = result['modes']['same_import']
        print(f"{result['case']}: baseline={result['baseline_v6']['count']}, probe={p['total']}, agree={p['agrees_with_v6_total']}, classes={len(p['rule_classes'])}, unchanged={result['source_unchanged']}")
        for rule in sorted(p['rule_classes'], key=lambda x: -x['locations'])[:8]:
            print('  ', rule['locations'], rule['description'][:110])
        if 'original_import_options' in result['modes']:
            q = result['modes']['original_import_options']
            print('  original dataset import options count=', q['total'], 'valid=', q['valid'])
        if p['errors']:
            print('  PROBE ERRORS:', p['errors'])
    print('NOTE: Rules/locations, control tests, and independent deck still need qualification.')
    return 0 if all('error' not in r and r['source_unchanged'] and r['modes']['same_import']['valid'] for r in all_results) else 2


if __name__ == '__main__':
    sys.exit(main())

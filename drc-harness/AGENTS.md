# AGENTS.md — KLayout work in this repo

This repository is a KLayout layout workspace. **Every KLayout action goes
through the `klayout` MCP server (klink). There is no other allowed path.**

---

## 0. Non-negotiable rules

1. **MCP only.** Read, create, modify, query, route, export, DRC and LVS —
   all of it through the `klayout` MCP tools.
2. **Never write raw Python (or Ruby) that touches KLayout.** This is absolute:
   - no `import pya` / `import klayout.db` / `import klayout.lay`
   - no `exec.python` MCP tool, even though the server exposes it
   - no `python -c`, no ad-hoc `.py` scripts, no notebooks, no macros
   - no `from klink import KLinkClient` client scripts
   - no `c.show()` / klive port 8082 pushes from Python
   - no parsing `.gds`, `.lyrdb`, or `.lvsdb` with Python to "get the answer"
   If a task seems impossible through the MCP, **stop and report the missing
   capability** — do not fall back to Python.
3. **Read the skills before acting.** They live in this directory and are
   authoritative for workflow details:
   - `.agents/skills/klayout/SKILL.md`
   - `.agents/skills/klayout-gdsfactory/SKILL.md`
   Read the relevant one(s) at the start of any non-trivial task. The
   `allowed-tools: bash python` line in those skill front-matter does **not**
   override rule 2 — in this repo the skills are knowledge, the MCP is the
   only execution surface.
4. **Structured state is the source of truth**, not screenshots and not
   guesses: `layout.info`, `cell.list` / `cell.tree`, `layer.list`,
   `shape.query`, `instance.query`, `selection.get`,
   `interaction.selection.latest/recent`, and tool results.
5. **No screenshots** unless the user explicitly asks for an image in this
   conversation. Never as a verification step.
6. **Ask before destructive actions** on anything you did not create
   (`layout.clear`, `view.close_tab`, `cell.delete`, `instance.delete`,
   `annotation.clear`). Never clear or close a user's working tab.

Tool naming: RPC names are dotted (`drc.run`, `shape.query`); the harness
exposes them as `tools.klayout.<snake_case>` (`tools.klayout.drc_run`,
`tools.klayout.shape_query`). Use the dotted name when talking about the
capability, the harness path when calling it.

---

## 1. Session start checklist

Call these before editing anything:

```text
klink.guide {}
layout.info {}
cell.list {}
layer.list {}
```

`klink.guide` reports what is open and the literal call for each intention.
Every orchestrated tool result also carries `next_action` — follow it
verbatim.

---

## 2. Tool map (use these, not scripts)

| Goal | MCP tool |
|---|---|
| What is open / refresh world view | `layout.info`, `view.list_tabs` |
| Cells and hierarchy | `cell.list`, `cell.tree`, `cell.create` |
| Layers and display | `layer.list`, `layer.display_list`, `layer.set_visible`, `layer.set_style`, `layer.load_lyp` |
| Place devices | `instance.insert_pcell`, `instance.insert_pcell_many`, `instance.insert`, `instance.insert_many` |
| Inspect placements | `instance.query` |
| Draw / inspect geometry | `shape.query`, `shape.insert_box`, `shape.insert_boxes`, `shape.insert_many` |
| Batches | batch variants above (one RPC, one undo step) |
| Devices from libraries | `pcell.libraries`, `pcell.list`, `pcell.info` |
| Connectivity / nets | `structdevice.declare_nets`, `structdevice.connect_nets`, `structdevice.lvs_check` |
| DRC | `drc.run` (see §3) |
| Save / load / export | `layout.save_file`, `layout.show_file`, `layout.import_file`, `layout.export_clean` |
| Selection / user intent | `selection.get`, `interaction.selection.latest`, `interaction.selection.recent` |

Use batch tools instead of one call per shape. Always use `*_um` (microns)
parameters.

---

## 3. Verification: DRC ("drc-full")

**drc-full** is the canonical full-deck DRC check: the *complete* rule deck
for the process, run on the live layout — not a spot check and not a
hand-picked subset. In this MCP server that is the **`drc.run`** RPC
(`tools.klayout.drc_run`). When the user says "run drc-full", run the full
deck; when they say "run the DRC", default to full.

### 3.1 How to call it (MCP only)

```jsonc
{
  "code": "<the full DRC deck, DRC-DSL source>",
  "output_rdb": "/abs/path/to/report.lyrdb",
  "result_mode": "summary",   // use "full" when item coordinates are needed
  "top_cell": "DEV_TEST"      // only in standalone mode (with source()); omit in interactive mode
}
```

Rules for the deck:

- `report("title", $output_rdb)` is the **first line** — without the second
  argument the RDB is never written and the run looks clean for the wrong
  reason.
- **Always use decimal points for dimensions**: `width(0.14)` is 0.14 µm;
  `width(14)` is database units and silently passes everything.
- Omit `source()` to audit the layout currently shown in the view
  (interactive mode). In interactive mode the engine audits *whatever cell
  the view shows* — call `view.show_cell` first if you need a specific cell.
- Include `source($input_layout)` only for standalone file audits, and then
  pass `input_layout` + `top_cell`.
- If the repo carries a deck file (e.g. `drc-full.drc` / `drc-full.lydrc`),
  pass its source as `code` — do not re-implement rules inline.

### 3.2 Getting the violations — through the MCP, always

The **same `drc.run` response already contains the violations**:

- `rdb_summary.total_items` — total violation count
- `rdb_summary.categories[]` — `{name, count}` per rule
- `rdb_summary.items[]` — per-item records (with `result_mode: "full"`,
  up to 200 items)
- `exception` — non-null means the deck failed; report it verbatim
- `rdb_file` — path to the `.lyrdb` if the user wants the native report

Do **not** open the `.lyrdb` with Python, grep it, or recompute violations.
If more detail is needed, re-run the same MCP call with
`result_mode: "full"` (optionally narrowed by the deck's rule set) and read
the response.

### 3.3 Verdict discipline

All-or-nothing:

- **PASS** only when `exception == null` **and** `total_items == 0`.
- **FAIL** otherwise: report the per-rule counts and, for `full` mode, the
  item locations. Never weaken, delete, or loosen a rule to make a run pass.
  If a rule is genuinely wrong for the process, say so explicitly, fix the
  deck, and re-run — and call out that the earlier pass was a deck bug.
- Scope rules with `exclude_around` only for device-region geometry, and say
  so in the result summary.
- State clearly that the verdict applies to the deck that was run; a
  hand-written test deck is not a foundry signoff deck.

---

## 4. Verification: LVS and connectivity

Use the MCP one-call tools; never re-implement extraction:

```text
structdevice.declare_nets { recent_sends: N, cell: "<cell>", conductors: [...], vias: [...] }
structdevice.connect_nets  { cell: "<cell>", conductors: [...], vias: [...], route_layer: "...", ... }
structdevice.lvs_check     { cell: "<cell>", conductors: [...], vias: [...], mode: "both" }
```

- `lvs_check` passes only on `match == true`.
- `ok: false` is a real finding, not a tool failure. Relay `problems`
  verbatim; never guess pairings, terminals, or coordinates.
- klink ships no process data: always pass the project's `conductors` /
  `vias`. Never invent a `ProcessProfile` or a conductor stack.

---

## 5. Editing discipline

1. `layout.info` → know the tab, top cell, dbu.
2. `layer.ensure` / `layer.load_lyp` before drawing on a new layer.
3. Prefer `instance.insert_pcell_many` / `shape.insert_boxes` for batches
   (single RPC, single undo step).
4. Keep placement coordinates in microns; verify with `instance.query`
   (bboxes, transforms) and `shape.query` (counts, bboxes) — not visually.
5. Re-check that intended connections actually overlap (bbox math from
   MCP query results) before claiming "connected".
6. Save with `layout.save_file` when the user wants the file updated, and
   report the path.
7. If a verification fails, fix the geometry through the MCP, then re-run
   the full check.

---

## 6. gdsfactory work (photonic components)

Reading/generating with gdsfactory is outside KLayout, but **pushing into
KLayout must still go through the MCP**:

- Allowed: run the project's gdsfactory generator to produce a GDS file on
  disk.
- Then load it with `layout.show_file` (new tab / replace) or merge it with
  `layout.import_file` — never `c.show()`, never `KLinkClient` routes.
- Place/route existing components with the photonics MCP tools
  (`photonics.connect`, `photonics.reroute`, `port.harvest_blackbox`) and
  the routing tools (`routing.*`).
- Verification: same rules as §3/§4 — MCP queries only.

---

## 7. Forbidden patterns (reject on sight)

```text
exec.python / exec.python(...)                     -> NO
import pya / klayout.db / klayout.lay              -> NO
python -c "..." / heredoc python                   -> NO
klink.KLinkClient(...) scripts                     -> NO
c.show() / localhost:8082 pushes                   -> NO
parsing .lyrdb / .lvsdb / .gds with Python         -> NO
screenshots as verification                        -> NO
weakening a DRC/LVS rule to force a pass           -> NO
deleting/closing the user's tab                    -> NO
```

If you catch yourself about to write Python "just to check" or "just to run
the deck", stop: the MCP call already exists or the capability is missing.
Report the gap.

---

## 8. When the MCP is unavailable or a tool is missing

1. Check `klink.status` / `klink.guide`.
2. If the connection is stale, ask the user to reload the klink plugin /
   restart the MCP server (see skill "Reload Rule").
3. Report what is missing and what you would have called. **Do not** work
   around it with Python, Bash, or direct file manipulation of layout
   formats.

---

## Appendix A — Reference `drc-full` call (SkyWater-style test deck)

Shape of a real full-deck run on the live view. The deck is DRC-DSL source
passed as a string — it is not Python. Replace the rule block with the
project's full deck; keep the call shape.

```jsonc
// tools.klayout.drc_run
{
  "code": "report(\"DEV_TEST full DRC\", $output_rdb)\n\ndiff   = input(65, 20)\npoly   = input(66, 20)\nlicon1 = input(66, 44)\nli1    = input(67, 20)\nmcon   = input(67, 44)\nmet1   = input(68, 20)\nnwell  = input(64, 20)\n\ndiff.width(0.15).output(\"diff.width < 0.15\")\ndiff.space(0.27).output(\"diff.space < 0.27\")\npoly.width(0.15).output(\"poly.width < 0.15\")\npoly.space(0.21).output(\"poly.space < 0.21\")\nlicon1.width(0.17).output(\"licon1.size < 0.17\")\nlicon1.space(0.19).output(\"licon1.space < 0.19\")\nli1.width(0.17).output(\"li1.width < 0.17\")\nli1.space(0.17).output(\"li1.space < 0.17\")\nmcon.width(0.17).output(\"mcon.size < 0.17\")\nmcon.space(0.19).output(\"mcon.space < 0.19\")\nmet1.width(0.14).output(\"met1.width < 0.14\")\nmet1.space(0.14).output(\"met1.space < 0.14\")\nmet1.enclosing(mcon, 0.03).output(\"met1.enclosing(mcon) < 0.03\")\nnwell.width(0.84).output(\"nwell.width < 0.84\")\n",
  "output_rdb": "/home/ig/work/klayout-mcp-test/drc_dev_test.lyrdb",
  "result_mode": "summary"
}
```

Read the verdict from the response:

```text
exception == null  AND  rdb_summary.total_items == 0   -> PASS
otherwise                                              -> FAIL (report categories)
```

For item locations re-call the same tool with `"result_mode": "full"` and
read `rdb_summary.items[]`. Never parse the `.lyrdb` yourself.

> Note: this SkyWater-style deck is a test deck, not a foundry signoff deck.
> Say so when reporting, and never present a hand-written deck's PASS as
> tapeout signoff.

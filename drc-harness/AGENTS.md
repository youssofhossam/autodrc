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

### 3.0 Which deck — resolve this before calling anything

`drc.run` is a DRC-DSL **escape hatch**, not a PDK-aware DRC: it executes
exactly the source you hand it. A result counts as "the DRC" of this project
only if that source is the project's canonical deck.

For this workspace the canonical deck is the Efabless/SkyWater sky130 deck:

| Role | Path |
|---|---|
| Rule deck (FEOL + BEOL + offgrid) | `/home/ig/.klayout/salt/Efabless_sky130/tech/sky130/drc/sky130A_mr.drc` |
| Toplevel wrapper that enables those groups | `/home/ig/.klayout/salt/Efabless_sky130/tech/sky130/macros/run_drc_full.lydrc` |
| Flags the wrapper sets | `$feol = "true"`, `$beol = "true"`, `$offgrid = "true"` |

- Pass the deck's **source text** as `code`. Reading a `.drc` / `.lydrc`
  file is text handling, not layout parsing — it is allowed and expected.
- No such deck on disk, or an unknown PDK? **Stop and ask.** Never invent
  rules, never re-use an example deck, never report a number.
- `sky130_drc.txt`, `*.lyrdb`, `*.lvsdb` are **reports** (marker / LVS
  databases), never decks. Never pass one as `code`, never read it as the
  rule source.
- Never present the output of a hand-written or example deck as "the DRC".
  Different thresholds and scoping make the counts meaningless: a 14-rule
  sketch of sky130 once reported 187 "violations" on `variant_0.gds` where
  the real deck reports 39, and every one of its categories was invented.
- `ledit.run_drc` is L-Edit's own rule set and returns a count only. It is
  not a substitute for `drc.run` on this layout.

### 3.1 How to call it (MCP only)

```jsonc
{
  "code": "<prologue from below + canonical deck source>",
  "output_rdb": "/abs/path/to/report.lyrdb",
  "result_mode": "summary",   // use "full" when item coordinates are needed
  "top_cell": "<top cell from layout.info>"  // standalone mode only
}
```

Rules for the deck:

- `report("title", <rdb path>)` must be called **with the second argument**
  before any `output()`. Without it the RDB is never written and the run
  looks clean for the wrong reason. The canonical deck already does this via
  `$report`; an inline deck must put it first.
- **Always use decimal points for dimensions**: `width(0.14)` is 0.14 µm;
  `width(14)` is database units and silently passes everything.
- `drc.run` string-substitutes exactly three names: `$input_layout`,
  `$output_rdb`, `$topcell` (from the parameters of the same name). The
  Efabless deck uses different names — `$input`, `$report`, `$top_cell` —
  plus the `$feol` / `$beol` / `$offgrid` flags, so give it a prologue:

  ```text
  $feol = "true"; $beol = "true"; $offgrid = "true"
  $input = "<layout path>"; $top_cell = "<cell>"; $report = "<rdb path>"
  <canonical deck source>
  ```

- `# %include` is resolved **relative to the script file**. `drc.run` writes
  your source to a temp file under `/tmp`, so the wrapper's
  `# %include ../drc/sky130A_mr.drc` will not resolve. Inline the deck source
  after the prologue — that is the guaranteed route. An **absolute**
  `# %include` may work, but confirm it by the category count (§3.2) before
  trusting the result.
- Omit `source()` to audit the layout currently shown in the view
  (interactive mode). In interactive mode the engine audits *whatever cell
  the view shows* — call `view.show_cell` first if you need a specific cell.
- Include `source($input_layout, $topcell)` only for standalone file audits,
  and then pass `input_layout` + `top_cell`.

### 3.2 Getting the violations — through the MCP, always

The **same `drc.run` response already contains the violations**:

- `rdb_summary.total_items` — total violation count
- `rdb_summary.categories[]` — `{name, count}` per rule
- `rdb_summary.items[]` — per-item records (with `result_mode: "full"`,
  up to 200 items)
- `exception` — non-null means the deck failed; report it verbatim
- `rdb_file` — path to the `.lyrdb` if the user wants the native report

Check that the deck actually ran before reading any count:

- A full sky130 run registers on the order of **~295 categories**, all named
  like PDK rules (`nwell.1`, `nwell.2a`, `licon.1`, `MR_licon.SP.1`, `li.3`).
- Empty or tiny `categories`, or names that read like English sentences
  (`diff.width < 0.15`, `met1.enclosing(mcon) < 0.03`), mean the wrong deck
  ran. That is a **broken run, not a clean layout** — fix the deck, re-run,
  and do not report the counts.

Do **not** open the `.lyrdb` with Python, grep it, or recompute violations.
If more detail is needed, re-run the same MCP call with
`result_mode: "full"` (optionally narrowed by the deck's rule set) and read
the response.

### 3.3 Verdict discipline

All-or-nothing:

- **PASS** only when `exception == null` **and** `total_items == 0` **and**
  the run used the canonical deck (§3.0).
- **FAIL** otherwise: report the per-rule counts and, for `full` mode, the
  item locations. Never weaken, delete, or loosen a rule to make a run pass.
  If a rule is genuinely wrong for the process, say so explicitly, fix the
  deck, and re-run — and call out that the earlier pass was a deck bug.
- Scope rules with `exclude_around` only for device-region geometry, and say
  so in the result summary.
- Every verdict names the deck that was run: deck path (or "inline, derived
  from `<path>`"), the deck's release string when it has one, the flags, the
  layout file, and the top cell. Counts from two different decks are never
  comparable, and quoting "39 vs 187 violations" without that context is
  misinformation.
- A hand-written test deck is not a foundry signoff deck. If the canonical
  deck is unavailable, say **"not verified"** — never substitute a sketch.

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
hand-writing a deck instead of using the PDK deck  -> NO
passing an .lyrdb / sky130_drc.txt as a deck       -> NO
swapping in ledit.run_drc for drc.run              -> NO
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

## Appendix A — `drc.run` call shape (NOT a deck)

Use this only for the *shape* of the request. The `code` value is a
placeholder: it must contain the canonical deck's source (§3.0) behind the
prologue (§3.1). There is deliberately no rule block here — a rule block
written from memory is not the project's DRC, and its counts are fiction.

```jsonc
// tools.klayout.drc_run
{
  "code": "$feol = \"true\"; $beol = \"true\"; $offgrid = \"true\"\n$input = \"/abs/path/layout.gds\"; $top_cell = \"<CELL>\"; $report = \"/abs/path/report.lyrdb\"\n<canonical deck source>",
  "output_rdb": "/abs/path/report.lyrdb",
  "result_mode": "summary"
}
```

Read the verdict from the response:

```text
exception == null  AND  rdb_summary.total_items == 0  AND  categories are PDK rules
    -> PASS
otherwise
    -> FAIL (report categories + deck identity)
```

For item locations re-call the same tool with `"result_mode": "full"` and
read `rdb_summary.items[]`. Never parse the `.lyrdb` yourself.

> Note: only the canonical PDK deck can produce a signoff-grade result. If
> you ran anything else, say so explicitly and do not present its PASS as a
> tapeout signoff.

# tools/

`pdk/` is **not** committed to git. The pinned SKY130A PDK is ~1.1 GB and one of
its files (`sky130_fd_io/gds/sky130_fd_io.gds`, 113 MB) exceeds GitHub's 100 MB
per-file limit.

The flow expects the PDK at `tools/pdk/sky130A`. Recreate it with one of:

```bash
# Option 1: volare (matches the pin recorded below)
volare enable --pdk sky130 0fe599b2afb6708d281543108caf8310912f54af
mkdir -p tools/pdk
ln -s ~/.volare/volare/sky130/versions/0fe599b2afb6708d281543108caf8310912f54af/sky130A tools/pdk/sky130A

# Option 2: ciel
ciel fetch --pdk-family sky130 --pdk-variant sky130A
mkdir -p tools/pdk
ln -s "$(ciel path --pdk-family sky130 --pdk-variant sky130A)" tools/pdk/sky130A
```

Or point the validator at any existing PDK with `--pdk <path>/sky130A`.

Pinned versions used to produce the stored evidence (`tools_versions.json`):

| Tool | Version |
| --- | --- |
| Magic | 8.3.691 (`/usr/local/bin/magic`) |
| Netgen | `PATH` (`/usr/local/bin/netgen`) |
| SKY130A PDK | `0fe599b2afb6708d281543108caf8310912f54af` |

The remaining files here (`Magic/`, `Netgen/`, `SKY130_PDK/`, `tools_versions.json`)
are only version markers.

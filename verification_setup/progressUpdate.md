**Graduation Project – Progress Update**

We started by investigating and running the original **OSIRIS verification flow**, with the goal of preparing a reliable dataset for benchmarking our DRC closure agent.

During this process, we identified several issues and limitations in the original Adapter and Validator implementations, so we developed improved versions of both.

**1. Adapter Improvements**

The Adapter is responsible for converting the original schematic SPICE representation into a format compatible with the device representation extracted from the GDS layout by Magic, allowing Netgen to perform LVS correctly.

The main improvements include:

- Improved handling of **multi-finger MOS devices (`nf`)**, including effective-width calculations.
- More explicit handling of **MOS width/length units and device parameters**, avoiding incorrect assumptions during conversion.
- Improved **MIM capacitor conversion** and basic compatibility checks against the SKY130 PDK.
- Additional checks to prevent unsupported or ambiguous device conversions from silently producing misleading LVS results.

**2. Validator Improvements**

We also identified issues in the original validation scripts. For example, they could classify an LVS result as PASS based on a successful final-match message, even when Netgen reported device property errors.

Our improved Validator includes:

- Stricter LVS result classification, including detection of property mismatches.
- Additional checks for top-cell ports, MOS geometry, and Magic GDS import warnings.
- Automated Magic extraction and Netgen LVS execution.
- More detailed verification reports and checks to ensure that the original input files remain unchanged.

**3. Current Limitations**

The improved Adapter is **not yet general-purpose**, and some limitations remain, particularly:

- Resistor model mapping and electrical equivalence between schematic and extracted layout devices.
- Complete qualification of multi-finger MOS representations.
- Some PDK/device compatibility and extraction cases.

Rather than spending the entire project timeline perfecting the Adapter, we decided to document these limitations and continue improving the verification flow incrementally as needed.

**4. Dataset Selection**

We initially selected seven circuits from each of five circuit families, with two layout variants per circuit.

However, three families contained resistor-related verification complications that we have not fully resolved. We therefore decided to postpone those families and initially focus on the two families without those complications in our selected samples:

- `five_transistors_ota`
- `ota_ff`

This gives us **28 candidate layouts with preliminary LVS PASS results and DRC violations**, which are suitable starting candidates for DRC closure benchmarking. Final verification qualification is still required before treating them as fully trusted references.

**5. Next Step – Benchmarking**

We are now moving toward establishing our initial benchmark by implementing and comparing three agentic loops with 3 diffrent llms

We will evaluate these approaches on the same selected layouts, measuring their ability to resolve DRC violations while preserving LVS correctness.

The initial benchmark will provide a baseline for comparing the models and identifying opportunities to improve our agent. In parallel, we will continue refining the Adapter, Validator, and dataset qualification whenever the experiments reveal relevant limitations.

**Overall, our current direction is to move from verification-flow preparation into actual agent benchmarking, while improving the supporting verification infrastructure incrementally rather than allowing it to delay the core project.**

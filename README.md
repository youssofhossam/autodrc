# AutoDRC — Autonomous DRC Debugging & Verified Repair
AutoDRC is an AI-powered system designed to assist with debugging and repairing Design Rule Check (DRC) violations in analog IC layouts.

The project aims to identify violation patterns, analyze their root causes, suggest appropriate repairs, and verify proposed changes through design verification tools.

## Objectives

- **DRC Analysis:** Parse and organize DRC violations.
- **Root-Cause Analysis:** Identify potential causes of violations.
- **Automated Repair:** Suggest and apply constrained layout repairs.
- **Verification:** Validate repairs using DRC and LVS checks.
- **Learning from Past Fixes:** Reuse knowledge from previously successful repairs.

## Proposed Workflow

1. Extract DRC violations from the layout verification tool.
2. Group related violations and analyze their root causes.
3. Generate candidate repair actions.
4. Apply repairs in a controlled environment.
5. Re-run verification to evaluate the results.
6. Keep successful repairs and roll back unsuccessful changes.

## Technology Stack

The technology stack is under development and will be finalized as the project progresses.

## Project Status

**Currently in development.**

This project is part of a graduation project exploring AI-assisted debugging and autonomous repair for analog IC layouts.

## Contributors

Developed by the AutoDRC project team.


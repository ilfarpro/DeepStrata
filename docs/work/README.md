# Rebuild working records

Evidence from the October 2026 rebuild (Phase A, Mac-first), kept because the acceptance tooling
still reads it. It describes revision `e0951ce` and the machine the work ran on, not the current
`main` or a released build. For installing Strata today, start at [docs/README.md](../README.md).

The Desktop (Tauri) app was removed after this evidence was written. Rows and files that mention it
record what was true then and are kept unchanged.

## Read in this order

1. [IMPLEMENTATION_STATUS.md](IMPLEMENTATION_STATUS.md) — the resume point of the rebuild. What was
   implemented, what is proven, what is not, and the exact commands. It is long; section 0 maps the
   original machine's paths and section 3 lists the blockers.
2. [ACCEPTANCE-EVIDENCE.md](ACCEPTANCE-EVIDENCE.md) — the generated acceptance matrix: one row per
   checklist requirement, with the evidence path for each result. The counts are 46 passed, 0
   failed, 15 pending hardware, 1 pending release, 3 unsupported and 2 not implemented (67 rows).
3. [PHASE-A-RETURN-PACKET.md](PHASE-A-RETURN-PACKET.md) — the handoff summary: what was selected,
   what was built, the acceptance standing and what Phase B must still do. Its count table was
   never filled in (it shows `?`); use the generated matrix for numbers.
4. [QUALIFICATION-KIT.md](QUALIFICATION-KIT.md) — the executable Phase B procedure for checks that
   need a machine with enough memory and disk to hold a model pack. This remains the checklist for
   real-model qualification.
5. [`evidence/`](evidence/) — captured output of the commands named in the documents above. These
   are raw logs and JSON from one machine, not templates to copy.

## Status

| File | Status |
| --- | --- |
| `IMPLEMENTATION_STATUS.md` | Historical snapshot at revision `e0951ce` on an Apple M4. Its `/Users/nikoveit/...` paths are from the author's machine. Do not treat its "next actions" as current work. |
| `ACCEPTANCE-EVIDENCE.md` | Generated; do not edit by hand. Regenerate with `packaging/acceptance_matrix.py` where the original handoff checklist exists (see below). |
| `PHASE-A-RETURN-PACKET.md` | Handoff record. Count placeholders were filled in the generated matrix, not here. |
| `QUALIFICATION-KIT.md` | Still the procedure for the real-model (Phase B) checks; the Mac could not run a model. |
| `evidence/` | 24 captured files of command output; their paths are cited from `packaging/acceptance_matrix.py`. Machine-specific and historical. |

## Regenerating the acceptance matrix

```sh
python packaging/acceptance_matrix.py           # rewrite docs/work/ACCEPTANCE-EVIDENCE.md
python packaging/acceptance_matrix.py --check   # fail if a checklist row has no result
```

The script reads `strata-acceptance-checklist.md` from the original handoff packet, which it
expects under `/Users/nikoveit/Documents/deepseek-harness` (it searches that tree). On any other
machine it stops with `cannot find strata-acceptance-checklist.md`; the generated file above is the
copy of record until the checklist is available.

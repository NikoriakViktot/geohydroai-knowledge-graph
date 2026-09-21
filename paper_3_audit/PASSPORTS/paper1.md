# Paper 1 passport — vertical frame and water-surface geometry

*Phase 0 deliverable, 2026-09-21. Numbers are quoted from OWN_EVIDENCE.csv; each traces to a hashed snapshot table.*

## 1. The question
Does harmonising gauges, ICESat-2, SWOT and a legacy survey into one vertical frame let a change in water level and longitudinal slope be measured reliably — and what does that measurement then show?

## 2. Own result
- **The regime transition.** Per-overpass longitudinal slope: Theil–Sen median +0.090 → +3.314 cm/km, difference +3.223 cm/km (95 % CI [+1.994, +5.071]), positive in 14/14 post-breach overpasses against 10/14 before. One overpass is the independent unit; each slope is a fit through six beam-median points.
- **The harmonisation itself**, which is the enabling method rather than preprocessing: the BS-77 → EVRF2019 offset varies 0.1715–0.2157 m along the reach, so a national constant is inadmissible; the documented SWOT PIXC chain reproduces RiverSP to −0.0010 m while three alternative chains do not; a reservoir-derived alignment constant does **not** transfer to Kherson (disjoint intervals).
- **Within-overpass heterogeneity** 0.117 → 0.397 m, a second geometric indicator independent of the slope fit.
- **The drawdown from orbit**: the SWOT calibration orbit crossed the outlet daily through the breach fortnight, 17.53 m (31 May) → 5.63 m (13 June).

## 3. Borrowed foundation
None. This is the base paper; papers 2, 4 and 5 borrow its frame.

## 4. Novelty boundary
Lehnigk, Pavelsky & Lang (2026, GRL) have already published SWOT observation of this flood below the dam. What is added here is the **outlet** series carried in the same frame as the gauges — stated before the result, not after.

## 5. Key figure and independent check
Per-date slope, pre against post, with the 95 % CI of the difference. The independent check is the historical free-surface curve: the digitised 1970 field survey reproduces the independently tabulated Table 20 profile to a median +0.022 m, and pre-breach ICESat-2 slopes are indistinguishable from zero over the same reach.

## 6. Open limitations
Slope–discharge after the breach is **NOT TESTABLE** (C-14): the DniproHES discharge record ends 2023-12-31 and all 14 post dates are 2024–25. The channel-restricted control (M1.4) has a CI including zero. Fragmentation counts are coverage-confounded and are reported descriptively or not at all. The EGG2015 raster in production has no citable upstream source (G2).

## 7. Verdict
**Sufficient evidence.** The strongest of the five. Its risk is presentation, not support: it must read as a measurement paper whose method is the frame, not as a technical note.

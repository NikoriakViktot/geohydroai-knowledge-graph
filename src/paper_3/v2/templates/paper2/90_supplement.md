# SUPPLEMENTARY MATERIAL

## S1. Machine tables and open items

Tables are generated from `OWN_EVIDENCE.csv`; every number in the main text carries a claim id that points at a snapshot table row. Outstanding items are listed in `PAPER2_OPEN_ITEMS.md`, parsed from the [PENDING] markers of this document.

## S2. Sentinel-1 water masks: baseline and rejected variants

This study is confined to the supplement because it is a methods result about SAR flood-water detection over freshly exposed sediment, not part of the hydraulic argument. The baseline classifier is an anchored two-class Fisher discriminant on VV and VH (γ⁰, terrain-flattened) with a midpoint cut and a 0.05 km² sieve. Variants add speckle filtering, majority filtering, minimum mapping units, same-orbit change detection, a terrain veto from FABDEM slope and height above nearest water, and — in a pilot on the dam-to-Kherson floodway — a logistic and a random-forest rejection stage evaluated leave-one-orbit-out against optical water.

{{claim:R1.1}}. Every change-detection variant fails the new-water recall guard in all four zones because the pre-event reference over bare sediment is itself dark. {{claim:R1.2}}. {{claim:R1.3}}. The chronic false-water object over the Oleshky sands is a land-cover-conditioned error that no cut on the backscatter axis removes; a rejection stage informed by land cover or terrain is required. These results are offered as a methods note for a future remote-sensing paper (theses T35–T36, article thesis AT11, scope `supplement_only`).


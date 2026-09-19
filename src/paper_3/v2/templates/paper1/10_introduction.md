# 1. INTRODUCTION

## 1.1 The Kakhovka Reservoir and the 2023 dam breach

The Kakhovka Reservoir was the lowest of the Dnipro cascade: at its normal impoundment level of 16.00 m (Baltic 1977) it held 18.19 km³ over 2 155 km², with a navigation drawdown level of 14.00 m and a dead-volume level of 12.7 m, as tabulated in the original design documentation. On 6 June 2023 the dam was breached and the pool drained within days. What remained was a river running through its former bed, a set of disconnected remnant water bodies, and some two thousand square kilometres of freshly exposed sediment.

## 1.2 Why water area is not enough

Post-breach studies of the site have characterised the event predominantly through planform quantities — water extent, exposed area and their change ({{cite:vyshnevskyi2023}}; {{cite:maksymenko2026}}). {{pending:OPEN41|§7.8 literature claim LK1.1/LK1.2 not yet supportable (RETRIEVAL_UNVALIDATED)|backs=M1.1,M3.1|section=1.2 Why water area is not enough|unblock_by=verify hold-out positive controls for T03, T19; run --step retrieve, calibrate, gate}} Equal water areas can correspond to fundamentally different hydraulic regimes: a level pool and a sloping channel may occupy the same footprint. A regime change is a change in the geometry of the water surface, and detecting it requires elevations that are commensurable across sensors and epochs.

## 1.3 Longitudinal water-surface geometry from spatial altimetry

ICESat-2 photon-counting lidar and SWOT Ka-band interferometry observe water-surface elevation along tracks and across swaths rather than at virtual stations, so they can resolve the longitudinal gradient of a reach directly ({{cite:scherer2022}}; {{cite:musaeus2024}}; {{cite:dhote2024}}), and reach-scale slope is now produced globally from ICESat-2 ({{cite:scherer2023}}) and mapped dynamically by SWOT ({{cite:jiang2025}}; {{cite:ledauphin2025}}) — provided that thousands of along-track measurements from one pass are not mistaken for thousands of independent observations, and provided that heights reported on different ellipsoids, geoids and permanent-tide conventions are reduced to one frame before they are compared. Hydraulic models informed by such elevations are only as good as that reduction ({{cite:bauergottwein2023}}).

## 1.4 The vertical reference as the enabling step

Gauge stages in Baltic 1977, ICESat-2 heights in a tide-free ellipsoidal system, SWOT heights with tide fields supplied but not applied, and a legacy survey reduced to an operating level are four surfaces. The corrections between them are of the same order as the hydraulic signal sought — centimetres per kilometre over tens of kilometres — so the harmonisation is not a preprocessing convenience but the method itself. Height-datum unification is a mature discipline in geodesy ({{cite:barzaghi2020}}; {{cite:featherstone2011}}; {{cite:schwabe2026}}), and the consequence of neglecting it — an apparent gradient produced by the levelling network rather than by the water — is documented ({{cite:penna2013}}).

## 1.5 Objectives

This paper asks three questions. (i) Did the water surface within the former reservoir footprint change hydraulic regime, and does the change persist? (ii) Can SWOT and ICESat-2 be read together once their vertical references are harmonised? (iii) Does the legacy hydrography constrain both the pre-breach surface and the reconstructed bed, and how accurate is that bed? Where the data cannot answer — the slope–discharge relation after the breach, a rate of bed change — the paper states the limitation instead of inferring an answer. What the new geometry and the vegetating surface mean for roughness, conveyance and stage is the subject of a companion paper that takes the bed reconstructed here as its terrain input.

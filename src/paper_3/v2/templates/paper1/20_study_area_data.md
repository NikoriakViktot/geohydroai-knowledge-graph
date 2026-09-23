# 2. STUDY AREA AND OBSERVATION GEOMETRY

The study system is registered in four analysis zones defined once in a geometry registry and loaded by every script: ZONE_1 (the former reservoir and the lower Dnipro, 11 412 km², with the 2 144 km² reservoir core), ZONE_2 (the Kherson delta, 1 677 km²), ZONE_3 (the Dnipro–Buh estuary, 7 621 km²) and ZONE_4 (the dam-to-Kherson floodway, defined as the June 2023 Sentinel-1 flood envelope plus a 1 km buffer, 695 km²). Chainage for the reservoir profiles is measured from the dam along a reservoir-axis centreline; the historical reach boundaries of the design documentation are placed on it where tie points allow — a limitation, since only two of five ties are usable ({{claim:X1.1}}) — and reach 5 lies outside the mapped domain. SWOT's fast-sampling calibration orbit crossed both the reservoir outlet and the reach below the dam daily through the breach fortnight, so the mission observed the drawdown and the flood wave as they happened; its science-orbit LakeSP passes continue over the residual water bodies from late July 2023. What the calibration orbit did not give is repeated coverage of the whole pool on one date, so SWOT constrains the outlet and the downstream profile rather than the reservoir-wide slope.

{{pending:FIG01|Figure 1 — study system, gauges, zones, ICESat-2 tracks by period, SWOT coverage|backs=V1.3,M1.1|section=2 Study area|produces=SWOT-DNIPRO scripts (V0 gauge network + SFig01 + Fig12 composite)|status=not_started|blocks_submission=yes}}

# 3. DATA

## 3.1 Gauges and sea posts

Six reservoir gauges share a zero of 12.000 m BS-77 and the Kherson post 80805 sits at −5.000 m BS-77 ({{claim:V1.3}}); the reservoir series end on 31 December 2021 and Kherson runs daily through 2025. The 2023 UkrHMI yearbooks for the seas and river mouths add the estuary posts, extracted grid by grid and checked against the printed monthly statistics; at Ochakiv the breach surge appears as {{claim:G3.1}}.

## 3.2 ICESat-2 ATL13 and ATL08

ATL13 v7 water-surface heights (ITRF2020, tide-free) are used in three samples that must not be confused: the slope sample (14 pre-breach, 5 drawdown and 14 post-breach overpasses with a chainage span of at least 20 km), the heterogeneity sample (all ATL13 dates in the footprint, 192 pre and 23 post) and the exposed-bed sample (28 tracks). 

## 3.3 SWOT

SWOT PIXC and RiverSP granules over Kherson (cycles 482, 511 and 521, April–May 2023) supply the cross-sensor and gauge validation. The Ukraine-clipped LakeSP archive exists but is not used here: a lake-averaged product is physically inappropriate for a sloping, fragmenting water body.

## 3.4 Sentinel-2 water masks

Sentinel-2 L2A scenes processed on a 20 m registry grid (EPSG:32636) with the BOA additive offset applied supply the coverage-gated water masks of the planform analysis (Section 4.6); 53 dates cover the former pool. The full surface classification, the Sentinel-1 masks and the supervised inundation work belong to Paper 3.

## 3.5 Legacy hydrography

Eight photographed pages of the Dnipro reservoirs monograph (Tables 19–21, Figures 13–16) supply the design values and the 1970 longitudinal free-surface survey used here as an independent check. The 7 514 chart soundings are the subject of the bed-reconstruction paper (Paper 2); this paper uses them only to establish which reduction level they carry (Section 5.7).


## 3.6 Geodetic reference data

One production transformation grid is used everywhere, `ua_2019z.asc`, distributed with the UA_KRON/NH to EVRF2019zero transformation {{cite:CRS_EU_UA_KRON_EVRF2019ZERO}}; sampled at the estuary posts it gives an {{claim:G1}}. The EGG2015 quasigeoid used for the ICESat-2 and SWOT branches is a 7 200 × 3 600 raster at 1′ × 1′ spanning 50° W–70° E and 25° N–85° N, which is the declared geometry of the full-resolution EGG2015 product; the acquisition record and licence of this particular copy could not be reconstructed (Section 7.9).

{{pending:TAB01|Table 1 — data sources (machine-generated from the snapshot inventories; must state the three ICESat-2 samples and the single full-coverage pre-breach Sentinel-2 date)|backs=V1.3,M1.1,M2.1,M3.1,S1.5|section=3 Data|produces=knoweledg_graf src/paper_3/v2/assemble.py TABLE_SPECS T1|status=not_started|blocks_submission=yes}}

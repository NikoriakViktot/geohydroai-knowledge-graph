# 2. STUDY AREA AND OBSERVATION GEOMETRY

The study system is registered in four analysis zones defined once in a geometry registry and loaded by every script: ZONE_1 (the former reservoir and the lower Dnipro, 11 412 km², with the 2 144 km² reservoir core), ZONE_2 (the Kherson delta, 1 677 km²), ZONE_3 (the Dnipro–Buh estuary, 7 621 km²) and ZONE_4 (the dam-to-Kherson floodway, defined as the June 2023 Sentinel-1 flood envelope plus a 1 km buffer, 695 km²). Chainage for the reservoir profiles is measured from the dam along a reservoir-axis centreline; the historical reach boundaries of the design documentation are placed on it where tie points allow ({{claim:X1.1}}), and reach 5 lies outside the mapped domain. SWOT's calibration-phase orbit never covered the pool, so SWOT enters this study only downstream at Kherson.

{{pending:FIG01|Figure 1 — study system, gauges, zones, ICESat-2 tracks by period, SWOT coverage|backs=V1.3,M1.1|section=2 Study area|produces=SWOT-DNIPRO scripts (V0 gauge network + SFig01 + Fig12 composite)|status=not_started|blocks_submission=yes}}

# 3. DATA

## 3.1 Gauges and sea posts

Six reservoir gauges share a zero of 12.000 m BS-77 and the Kherson post 80805 sits at −5.000 m BS-77 ({{claim:V1.3}}); the reservoir series end on 31 December 2021 and Kherson runs daily through 2025. The 2023 UkrHMI yearbooks for the seas and river mouths add the estuary posts, extracted grid by grid and checked against the printed monthly statistics; at Ochakiv the breach surge appears as {{claim:G3.1}}.

## 3.2 ICESat-2 ATL13 and ATL08

ATL13 v7 water-surface heights (ITRF2020, tide-free) are used in three samples that must not be confused: the slope sample (14 pre-breach, 5 drawdown and 14 post-breach overpasses with a chainage span of at least 20 km), the heterogeneity sample (all ATL13 dates in the footprint, 192 pre and 23 post) and the exposed-bed sample (28 tracks). ATL08 terrain and canopy segments at 100 m and 20 m were layered over all four zones ({{claim:H1.1.n}}).

## 3.3 SWOT

SWOT PIXC and RiverSP granules over Kherson (cycles 482, 511 and 521, April–May 2023) supply the cross-sensor and gauge validation. The Ukraine-clipped LakeSP archive exists but is not used here: a lake-averaged product is physically inappropriate for a sloping, fragmenting water body.

## 3.4 Sentinel-2 and Sentinel-1

Sentinel-2 L2A scenes were processed on a 20 m registry grid (EPSG:32636) with the BOA additive offset applied, into seven indices (NDVI, NDWI, MNDWI, NDMI, BSI, AWEIsh, NDTI), a ten-class surface classification and regime composites per zone; 53 dates cover the former pool. Sentinel-1 RTC γ⁰ (VV, VH) was classified with an anchored two-class discriminant and its variants (Section 4.11). Dynamic World annual composites for 2022–2024 and FABDEM-derived slope and height-above-nearest-water complete the surface layers.

## 3.5 Legacy hydrography

Eight photographed pages of the Dnipro reservoirs monograph (Tables 19–21, Figures 13–16) and 7 514 S-57 chart soundings, whose survey epoch is unrecorded ({{claim:X2.1}}), are the historical sources. Which reduction level the soundings carry is not stated in the source and is established in Section 5.7.

## 3.6 Geodetic reference data

{{claim:G1}} The EGG2015 quasigeoid grid used for the ICESat-2 and SWOT branches is the 1′×1′ raster whose provenance is discussed in Section 7.10.

{{pending:TAB01|Table 1 — data sources (machine-generated from the snapshot inventories; must state the three ICESat-2 samples and the single full-coverage pre-breach Sentinel-2 date)|backs=V1.3,M1.1,M2.1,M3.1,S1.5|section=3 Data|produces=knoweledg_graf src/paper_3/v2/assemble.py TABLE_SPECS T1|status=not_started|blocks_submission=yes}}

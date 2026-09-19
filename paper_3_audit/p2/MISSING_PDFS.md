# Статті для статті 2, які не вдалося завантажити

Згенеровано 2026-09-19 з `data/paper_3_audit/p2/harvest/harvest_candidates.parquet`.
Усі вони **пройшли скринінг on-topic** (розпізнавальна сімʼя термінів + щільність), тобто
це не сміття з пошуку, а релевантні роботи, яких бракує в корпусі.

Куди класти PDF, якщо дістанете: `data/literature/pdf_missing/<slug>.pdf`, де `slug` — це DOI
з заміною `/` на `_` (наприклад `10.1061_(asce)0733-9429(1999)125:5(443).pdf`). Далі
`python -m src.paper_3.cli --step grobid --out data/paper_3_audit/p2`.

Скорочення тез: **T25** — заростання дна, **T26** — woody / land-cover, **T27** — точність класифікації, **T28** — ATL08, **T29** — DEM / берегова лінія, **T30** — крос-валідація DEM, **T31** — waterline-батиметрія, **T32** — ширина русла, **T33** — Manning / опір потоку, **T34** — шорсткість → рівні.

**Разом: 98** (67 платних, 31 відмовили при завантаженні).

## 1. Платні / без відкритого PDF (67)

OpenAlex не знає жодної OA-копії. Потрібен доступ через бібліотеку або запит автору.

| № | DOI | Рік | Назва | Журнал | Тези | Цитувань |
|---|---|---|---|---|---|---|
| 1 | [10.15421/032510](https://doi.org/10.15421/032510) | 2025 | Ecological and structural transformation of floodplain forests of Khortytsia island under post-catastrophic di | Ecology and Noospherology | T25 | 0 |
| 2 | [10.3390/s24051587](https://doi.org/10.3390/s24051587) | 2024 | Automated Mapping of Land Cover Type within International Heterogenous Landscapes Using Sentinel-2 Imagery wit | Sensors | T26 | 11 |
| 3 | [10.1061/(asce)0733-9429(2002)128:5(500)](https://doi.org/10.1061/(asce)0733-9429(2002)128:5(500)) | 2002 | Hydraulic Resistance of Flow in Channels with Cylindrical Roughness | Journal of Hydraulic Engineering | T33 | 418 |
| 4 | [10.1061/jyceaj.0004397](https://doi.org/10.1061/jyceaj.0004397) | 1975 | Analysis of Flow through Vegetation | Journal of the Hydraulics Division | T33 | 407 |
| 5 | [10.1061/(asce)0733-9429(1990)116:5(691)](https://doi.org/10.1061/(asce)0733-9429(1990)116:5(691)) | 1990 | Overland Flow in Wetlands: Vegetation Resistance | Journal of Hydraulic Engineering | T33 | 280 |
| 6 | [10.1016/s0169-555x(02)00333-1](https://doi.org/10.1016/s0169-555x(02)00333-1) | 2003 | Large woody debris and flow resistance in step-pool channels, Cascade Range, Washington | Geomorphology | T33 | 276 |
| 7 | [10.1002/esp.3633](https://doi.org/10.1002/esp.3633) | 2014 | Effects of vegetation on flow and sediment transport: comparative analyses and validation of predicting models | Earth Surface Processes and Landforms | T33 | 239 |
| 8 | [10.1061/(asce)0733-9429(1999)125:5(443)](https://doi.org/10.1061/(asce)0733-9429(1999)125:5(443)) | 1999 | Effect of Riparian Vegetation on Flow Resistance and Flood Potential | Journal of Hydraulic Engineering | T33 | 236 |
| 9 | [10.1061/(asce)0733-9429(1997)123:1(51)](https://doi.org/10.1061/(asce)0733-9429(1997)123:1(51)) | 1997 | Nonrigid, Nonsubmerged, Vegetative Roughness on Floodplains | Journal of Hydraulic Engineering | T33 | 230 |
| 10 | [10.1061/(asce)0733-9429(2000)126:10(732)](https://doi.org/10.1061/(asce)0733-9429(2000)126:10(732)) | 2000 | Friction Factors for Coniferous Trees along Rivers | Journal of Hydraulic Engineering | T33 | 202 |
| 11 | [10.1061/jyceaj.0005444](https://doi.org/10.1061/jyceaj.0005444) | 1980 | Biomechanics of Vegetative Channel Linings | Journal of the Hydraulics Division | T33 | 200 |
| 12 | [10.1061/(asce)0733-9429(1995)121:4(341)](https://doi.org/10.1061/(asce)0733-9429(1995)121:4(341)) | 1995 | Prediction of Effects of Woody Debris Removal on Flow Resistance | Journal of Hydraulic Engineering | T33 | 193 |
| 13 | [10.1080/00221686.2001.9628292](https://doi.org/10.1080/00221686.2001.9628292) | 2001 | Turbulent structures in partly vegetated open-channel flows with LDA and PI V measurements | Journal of Hydraulic Research | T33 | 186 |
| 14 | [10.1029/2000wr900153](https://doi.org/10.1029/2000wr900153) | 2000 | Stress partitioning in streams by large woody debris | Water Resources Research | T33 | 174 |
| 15 | [10.1002/hyp.1201](https://doi.org/10.1002/hyp.1201) | 2003 | Two‐dimensional hydraulic flood modelling using a finite‐element mesh decomposed according to vegetation and t | Hydrological Processes | T33 | 157 |
| 16 | [10.1029/2005wr004278](https://doi.org/10.1029/2005wr004278) | 2006 | Flow resistance dynamics in step‐pool channels: 2. Partitioning between grain, spill, and woody debris resista | Water Resources Research | T33 | 150 |
| 17 | [10.1029/2005wr004277](https://doi.org/10.1029/2005wr004277) | 2006 | Flow resistance dynamics in step‐pool stream channels: 1. Large woody debris and controls on total resistance | Water Resources Research | T33 | 124 |
| 18 | [10.1002/aqc.3270020203](https://doi.org/10.1002/aqc.3270020203) | 1992 | Effects of large woody debris removal on physical characteristics of a sand‐bed river | Aquatic Conservation Marine and Freshwater | T33 | 120 |
| 19 | [10.1061/jyceaj.0004488](https://doi.org/10.1061/jyceaj.0004488) | 1976 | Flow Resistance in Broad Shallow Grassed Channels | Journal of the Hydraulics Division | T33 | 111 |
| 20 | [10.1061/(asce)0733-9437(1992)118:5(733)](https://doi.org/10.1061/(asce)0733-9437(1992)118:5(733)) | 1992 | Modern Approach to Design of Grassed Channels | Journal of Irrigation and Drainage Enginee | T33 | 99 |
| 21 | [10.13031/2013.34321](https://doi.org/10.13031/2013.34321) | 1981 | Flow Resistance in Vegetated Waterways | Transactions of the ASAE | T33 | 87 |
| 22 | [10.1680/eacm.8.00006](https://doi.org/10.1680/eacm.8.00006) | 2011 | Vegetated flows in their environmental context: a review | Proceedings of the Institution of Civil En | T33 | 86 |
| 23 | [10.1002/hyp.1049](https://doi.org/10.1002/hyp.1049) | 2002 | Measuring the flow resistance of submerged grass | Hydrological Processes | T33 | 78 |
| 24 | [10.1061/(asce)0733-9429(1996)122:10(583)](https://doi.org/10.1061/(asce)0733-9429(1996)122:10(583)) | 1996 | Predicting Stage-Discharge Curves in Channels with Bank Vegetation | Journal of Hydraulic Engineering | T33 | 76 |
| 25 | [10.1002/rrr.3450010303](https://doi.org/10.1002/rrr.3450010303) | 1987 | Hydraulic effects of aquatic weeds in U.K. rivers | Regulated Rivers Research & Management | T33 | 76 |
| 26 | [10.13031/2013.36014](https://doi.org/10.13031/2013.36014) | 1976 | A Theory of Flow Resistance for Vegetated Channels | Transactions of the ASAE | T33 | 67 |
| 27 | [10.1016/s1001-6058(08)60052-9](https://doi.org/10.1016/s1001-6058(08)60052-9) | 2008 | Characteristics of Flow Resistance in Open Channels with Non-Submerged Rigid Vegetation | Journal of Hydrodynamics | T33 | 66 |
| 28 | [10.1111/j.1752-1688.1998.tb04164.x](https://doi.org/10.1111/j.1752-1688.1998.tb04164.x) | 1998 | EFFECT OF WOODY DEBRIS ENTRAPMENT ON FLOW RESISTANCE1 | JAWRA Journal of the American Water Resour | T33 | 62 |
| 29 | [10.1002/esp.3717](https://doi.org/10.1002/esp.3717) | 2015 | Hydraulic and geomorphic processes in an overbank flood along a meandering, gravel‐bed river: implications for | Earth Surface Processes and Landforms | T33 | 60 |
| 30 | [10.1061/(asce)0733-9429(2006)132:2(163)](https://doi.org/10.1061/(asce)0733-9429(2006)132:2(163)) | 2006 | Functional Relationships of Resistance in Wide Flood Plains with Rigid Unsubmerged Vegetation | Journal of Hydraulic Engineering | T33 | 55 |
| 31 | [10.1002/hyp.9256](https://doi.org/10.1002/hyp.9256) | 2012 | A hydro‐economic modelling framework for flood damage estimation and the role of riparian vegetation | Hydrological Processes | T33 | 54 |
| 32 | [10.1029/2020wr027613](https://doi.org/10.1029/2020wr027613) | 2020 | Drag Coefficient of Emergent Flexible Vegetation in Steady Nonuniform Flow | Water Resources Research | T33 | 51 |
| 33 | [10.1061/(asce)hy.1943-7900.0001058](https://doi.org/10.1061/(asce)hy.1943-7900.0001058) | 2015 | Flow–Vegetation–Sediment Interaction in a Cohesive Compound Channel | Journal of Hydraulic Engineering | T33 | 47 |
| 34 | [10.3390/rs70100836](https://doi.org/10.3390/rs70100836) | 2015 | Use of Radarsat-2 and Landsat TM Images for Spatial Parameterization of Manning’s Roughness Coefficient in Hyd | Remote Sensing | T33 | 41 |
| 35 | [10.1007/s10652-017-9534-z](https://doi.org/10.1007/s10652-017-9534-z) | 2017 | Drag coefficient for rigid vegetation in subcritical open-channel flow | Environmental Fluid Mechanics | T33 | 40 |
| 36 | [10.13031/2013.34681](https://doi.org/10.13031/2013.34681) | 1980 | Tractive Force Design of Vegetated Channels | Transactions of the ASAE | T33 | 36 |
| 37 | [10.1061/(asce)hy.1943-7900.0000457](https://doi.org/10.1061/(asce)hy.1943-7900.0000457) | 2011 | Bulk Flow Resistance in Vegetated Channels: Analysis of Momentum Balance Approaches Based on Data Obtained in  | Journal of Hydraulic Engineering | T33 | 35 |
| 38 | [10.1002/eco.2474](https://doi.org/10.1002/eco.2474) | 2022 | Experimental investigation of 3D flow properties around emergent rigid vegetation | Ecohydrology | T33 | 34 |
| 39 | [10.1061/(asce)hy.1943-7900.0001597](https://doi.org/10.1061/(asce)hy.1943-7900.0001597) | 2019 | Evaluating Riparian Vegetation Roughness Computation Methods Integrated within HEC-RAS | Journal of Hydraulic Engineering | T33 | 33 |
| 40 | [10.1016/j.flowmeasinst.2019.101610](https://doi.org/10.1016/j.flowmeasinst.2019.101610) | 2019 | Assessing flow resistance law in vegetated channels by dimensional analysis and self-similarity | Flow Measurement and Instrumentation | T33 | 29 |
| 41 | [10.1080/00221686.2010.531101](https://doi.org/10.1080/00221686.2010.531101) | 2010 | Modelling vegetation effects in irregular meandering river | Journal of Hydraulic Research | T33 | 28 |
| 42 | [10.1080/15715124.2022.2143512](https://doi.org/10.1080/15715124.2022.2143512) | 2022 | New formulas addressing flow resistance of floodplain vegetation from emergent to submerged conditions | International Journal of River Basin Manag | T33 | 24 |
| 43 | [10.1061/(asce)hy.1943-7900.0000712](https://doi.org/10.1061/(asce)hy.1943-7900.0000712) | 2012 | Flow Resistance and Velocity Structure in Shallow Lakes with Flexible Vegetation under Surface Shear Action | Journal of Hydraulic Engineering | T33 | 20 |
| 44 | [10.1016/j.aqpro.2015.02.102](https://doi.org/10.1016/j.aqpro.2015.02.102) | 2015 | Prediction of Velocity Distribution in Straight Channel with Rigid Vegetation | Aquatic Procedia | T33 | 15 |
| 45 | [10.1080/15715124.2019.1672704](https://doi.org/10.1080/15715124.2019.1672704) | 2019 | Manning's roughness coefficient for ecological subsurface channel with modules | International Journal of River Basin Manag | T33 | 15 |
| 46 | [10.1002/hyp.14009](https://doi.org/10.1002/hyp.14009) | 2020 | A full‐scale study of Darcy‐Weisbach friction factor for channels vegetated by riparian species | Hydrological Processes | T33 | 14 |
| 47 | [10.13031/2013.33954](https://doi.org/10.13031/2013.33954) | 1983 | Vegetation Lined Channel Design Procedures | Transactions of the ASAE | T33 | 12 |
| 48 | [10.1063/5.0263237](https://doi.org/10.1063/5.0263237) | 2025 | Hydrodynamics of turbulent flow in channels with submerged flexible vegetation canopy | Physics of Fluids | T33 | 11 |
| 49 | [10.1080/09715010.2022.2066482](https://doi.org/10.1080/09715010.2022.2066482) | 2022 | Estimation of drag coefficient of emergent and submerged vegetation patches with various densities and arrange | ISH Journal of Hydraulic Engineering | T33 | 11 |
| 50 | [10.1002/hyp.8041](https://doi.org/10.1002/hyp.8041) | 2011 | A soft hydrological monitoring approach for comparing runoff on a network of small poorly gauged catchments | Hydrological Processes | T33 | 10 |
| 51 | [10.1002/esp.3642](https://doi.org/10.1002/esp.3642) | 2014 | Patterns of bedload entrainment and transport in forested headwater streams of the Columbia Mountains, Canada | Earth Surface Processes and Landforms | T33 | 10 |
| 52 | [10.1080/15715124.2011.648775](https://doi.org/10.1080/15715124.2011.648775) | 2011 | Effects of submerged tropical macrophytes on flow resistance and velocity profiles in open channels | International Journal of River Basin Manag | T33 | 9 |
| 53 | [10.2478/johh-2024-0010](https://doi.org/10.2478/johh-2024-0010) | 2024 | Flow resistance of emergent rigid vegetation in steady flow | Journal of Hydrology and Hydromechanics | T33 | 6 |
| 54 | [10.1680/wama.2006.159.4.211](https://doi.org/10.1680/wama.2006.159.4.211) | 2006 | Reducing uncertainty in the hydraulic analysis of canals | Proceedings of the Institution of Civil En | T33 | 5 |
| 55 | [10.1109/ictsd.2015.7095845](https://doi.org/10.1109/ictsd.2015.7095845) | 2015 | Analysis of different roughness coefficients' variation in an open channel with vegetation |  | T33 | 3 |
| 56 | [10.5194/nhessd-1-5855-2013](https://doi.org/10.5194/nhessd-1-5855-2013) | 2013 | A hydro-sedimentary modelling system for flash flood propagation and hazard estimation under different agricul |  | T33 | 1 |
| 57 | [10.4028/www.scientific.net/amr.599.716](https://doi.org/10.4028/www.scientific.net/amr.599.716) | 2012 | Longitudinal Velocity Distribution and Manning's 'n' Coefficient in Open Channel with Unsubmerged Vegetation | Advanced materials research | T33 | 0 |
| 58 | [10.65540/jar.v23i.574](https://doi.org/10.65540/jar.v23i.574) | 2022 | Review on Resistance Force to Open Channel Flow through Emergent Vegetation | مجلة البحوث الأكاديمية | T33 | 0 |
| 59 | [10.1002/eco.70198](https://doi.org/10.1002/eco.70198) | 2026 | Flow Structure and Manning Coefficient in Open‐Channel Flows With Staggered Tall‐Short Vegetation | Ecohydrology | T33 | 0 |
| 60 | [10.1088/1755-1315/1453/1/012043](https://doi.org/10.1088/1755-1315/1453/1/012043) | 2025 | Hydraulic Characteristics of Vegetated Channel along Persiaran Gaafar Baba at Universiti Tun Hussein Onn Malay | IOP Conference Series Earth and Environmen | T33 | 0 |
| 61 | [10.1093/treephys/23.16.1113](https://doi.org/10.1093/treephys/23.16.1113) | 2003 | Ecophysiology of riparian cottonwoods: stream flow dependency, water relations and restoration | Tree Physiology | T34 | 296 |
| 62 | [10.1002/rra.778](https://doi.org/10.1002/rra.778) | 2004 | Assessment of the effects of cyclic floodplain rejuvenation on flood levels and biodiversity along the Rhine R | River Research and Applications | T34 | 140 |
| 63 | [10.1029/2024wr037742](https://doi.org/10.1029/2024wr037742) | 2024 | Sediment Transport and Flood Risk: Impact of Newly Constructed Embankments on River Morphology and Flood Dynam | Water Resources Research | T34 | 24 |
| 64 | [10.1680/wama.2004.157.1.21](https://doi.org/10.1680/wama.2004.157.1.21) | 2004 | Conveyance of a managed vegetated two-stage river channel | Proceedings of the Institution of Civil En | T34 | 22 |
| 65 | [10.1007/s13280-010-0120-6](https://doi.org/10.1007/s13280-010-0120-6) | 2011 | The Influence of Floodplain Vegetation Succession on Hydraulic Roughness: Is Ecosystem Rehabilitation in Dutch | AMBIO | T34 | 20 |
| 66 | [10.2747/0272-3646.23.1.59](https://doi.org/10.2747/0272-3646.23.1.59) | 2002 | Historical Changes in Flood Power and Riparian Vegetation in Lower Harris Wash, Escalante River Basin, Utah | Physical Geography | T34 | 11 |
| 67 | [10.1016/j.psep.2024.02.056](https://doi.org/10.1016/j.psep.2024.02.056) | 2024 | Framework for comprehensive assessment of ecological water conveyance based on long-term evolution forecast of | Process Safety and Environmental Protectio | T34 | 1 |

## 2. Позначені OA, але видавець не віддав файл (31)

OpenAlex каже is_oa=true, але сервер повернув 403, HTML або таймаут. Часто відкриваються вручну в браузері.

| № | DOI | Рік | Назва | Журнал | Тези | Цитувань |
|---|---|---|---|---|---|---|
| 1 | [10.3390/drones6050100](https://doi.org/10.3390/drones6050100) | 2022 | UAV and Structure-From-Motion Photogrammetry Enhance River Restoration Monitoring: A Dam Removal Study | Drones | T25 | 28 |
| 2 | [10.3390/rs11232807](https://doi.org/10.3390/rs11232807) | 2019 | Improved Mapping of Mountain Shrublands Using the Sentinel-2 Red-Edge Band | Remote Sensing | T26 | 64 |
| 3 | [10.3390/rs14164005](https://doi.org/10.3390/rs14164005) | 2022 | Temporally-Consistent Annual Land Cover from Landsat Time Series in the Southern Cone of South America | Remote Sensing | T26 | 14 |
| 4 | [10.3390/rs12091367](https://doi.org/10.3390/rs12091367) | 2020 | Land Use/Land Cover Mapping Using Multitemporal Sentinel-2 Imagery and Four Classification Methods—A Case Stud | Remote Sensing | T27 | 139 |
| 5 | [10.1029/2022wr033168](https://doi.org/10.1029/2022wr033168) | 2023 | Physics‐Informed Neural Networks of the Saint‐Venant Equations for Downscaling a Large‐Scale River Model | Water Resources Research | T29 | 115 |
| 6 | [10.1002/hyp.1270](https://doi.org/10.1002/hyp.1270) | 2003 | Floodplain friction parameterization in two‐dimensional river flood models using vegetation heights derived fr | Hydrological Processes | T33 | 209 |
| 7 | [10.1029/2001wr001238](https://doi.org/10.1029/2001wr001238) | 2003 | Influence of step composition on step geometry and flow resistance in step‐pool streams of the Washington Casc | Water Resources Research | T33 | 189 |
| 8 | [10.1061/(asce)0733-9429(2005)131:7(554)](https://doi.org/10.1061/(asce)0733-9429(2005)131:7(554)) | 2005 | Flow Resistance Law in Channels with Flexible Submerged Vegetation | Journal of Hydraulic Engineering | T33 | 154 |
| 9 | [10.1002/2016wr020090](https://doi.org/10.1002/2016wr020090) | 2017 | A new model for predicting the drag exerted by vegetation canopies | Water Resources Research | T33 | 152 |
| 10 | [10.1002/hyp.5820](https://doi.org/10.1002/hyp.5820) | 2005 | Analysis of Manning coefficient for small-depth flows on vegetated beds | Hydrological Processes | T33 | 47 |
| 11 | [10.3390/rs13132616](https://doi.org/10.3390/rs13132616) | 2021 | Estimating Floodplain Vegetative Roughness Using Drone-Based Laser Scanning and Structure from Motion Photogra | Remote Sensing | T33 | 25 |
| 12 | [10.3741/jkwra.2012.45.2.137](https://doi.org/10.3741/jkwra.2012.45.2.137) | 2012 | Derivation of Roughness Coefficient Relationships Using Field Data in Vegetated Rivers | Journal of Korea Water Resources Associati | T33 | 22 |
| 13 | [10.1007/s10652-022-09898-7](https://doi.org/10.1007/s10652-022-09898-7) | 2022 | Hydrodynamic forces on emergent cylinders in non-uniform flow | Environmental Fluid Mechanics | T33 | 19 |
| 14 | [10.3390/w10050556](https://doi.org/10.3390/w10050556) | 2018 | The Impact of Shrubby Floodplain Vegetation Growth on the Discharge Capacity of River Valleys | Water | T33 | 18 |
| 15 | [10.3390/w14244017](https://doi.org/10.3390/w14244017) | 2022 | Influence of Water Depth and Slope on Roughness—Experiments and Roughness Approach for Rain-on-Grid Modeling | Water | T33 | 15 |
| 16 | [10.1029/2023wr036879](https://doi.org/10.1029/2023wr036879) | 2024 | Drag Force on Submerged Flexible Vegetation in an Open‐Channel Flow | Water Resources Research | T33 | 14 |
| 17 | [10.1016/j.flowmeasinst.2023.102308](https://doi.org/10.1016/j.flowmeasinst.2023.102308) | 2023 | Flow resistance due to shrubs and woody vegetation | Flow Measurement and Instrumentation | T33 | 14 |
| 18 | [10.1016/j.ecolind.2021.107472](https://doi.org/10.1016/j.ecolind.2021.107472) | 2021 | Sediment transport and roughness coefficients generated by flexible vegetation patches in the emergent and sub | Ecological Indicators | T33 | 10 |
| 19 | [10.1002/rra.2868](https://doi.org/10.1002/rra.2868) | 2015 | Drag Forces on Large Cylinders | River Research and Applications | T33 | 10 |
| 20 | [10.1002/eco.2646](https://doi.org/10.1002/eco.2646) | 2024 | Flow resistance law in channels with emergent rigid vegetation | Ecohydrology | T33 | 7 |
| 21 | [10.3390/w14223727](https://doi.org/10.3390/w14223727) | 2022 | Towards i5 Ecohydraulics: Field Determination of Manning’s Roughness Coefficient, Drag Force, and Macroinverte | Water | T33 | 5 |
| 22 | [10.1002/hyp.14883](https://doi.org/10.1002/hyp.14883) | 2023 | Flow resistance of flexible vegetation in real‐scale drainage channels | Hydrological Processes | T33 | 4 |
| 23 | [10.32657/10356/50541](https://doi.org/10.32657/10356/50541) | 2012 | Characteristics of hydraulic resistance and velocity profile in vegetated open-channel flows |  | T33 | 4 |
| 24 | [10.1002/rvr2.32](https://doi.org/10.1002/rvr2.32) | 2023 | Flow resistance law in channels with fully submerged and rigid vegetation | River | T33 | 3 |
| 25 | [10.25148/etd.fi09120822](https://doi.org/10.25148/etd.fi09120822) | 2009 | Integrated Surface-Ground Water Modeling in Wetlands With Improved Methods to Simulate Vegetative Resistance t |  | T33 | 3 |
| 26 | [10.1002/eco.70120](https://doi.org/10.1002/eco.70120) | 2025 | Flow Resistance due to Aquatic Vegetation in Streams and Flumes | Ecohydrology | T33 | 2 |
| 27 | [10.31577/ahs-2021-0022.01.0007](https://doi.org/10.31577/ahs-2021-0022.01.0007) | 2021 | Influence of submerged vegetation on the Manning´s roughness coefficient for Gabčíkovo – Topoľníky channel | Acta hydrologica Slovaca | T33 | 2 |
| 28 | [10.1002/hyp.70547](https://doi.org/10.1002/hyp.70547) | 2026 | Investigation on Flow Resistance of Large‐Scale Channels With Unmanaged Spontaneous Vegetation | Hydrological Processes | T33 | 0 |
| 29 | [10.1029/2007jg000543](https://doi.org/10.1029/2007jg000543) | 2008 | Retrieval of vegetative fluid resistance terms for rigid stems using airborne lidar | Journal of Geophysical Research Atmosphere | T33;T34 | 32 |
| 30 | [10.1002/hyp.14613](https://doi.org/10.1002/hyp.14613) | 2022 | Longitudinal dispersion affected by willow patches of low areal coverage | Hydrological Processes | T34 | 11 |
| 31 | [10.3390/hydrology8040176](https://doi.org/10.3390/hydrology8040176) | 2021 | Riparian Vegetation Density Mapping of an Extremely Densely Vegetated Confined Floodplain | Hydrology | T34 | 10 |

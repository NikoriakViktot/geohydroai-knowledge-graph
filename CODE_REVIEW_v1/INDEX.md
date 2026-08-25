# CODE_REVIEW_v1 — Навігація

**Дата ревю**: 2026-06-11 | **Обсяг**: 34 пакети, 226 файлів, ~55K LOC, 606 тестів
**Конвенція**: підсумок і плани — українською; технічні модульні ревю — англійською (за зразком docs_v2 / AUDIT_v1)

| Документ | Зміст | Оцінка модуля |
|----------|-------|---------------|
| [00_EXECUTIVE_SUMMARY.md](00_EXECUTIVE_SUMMARY.md) | Загальна оцінка 6.5/10, топ-10 проблем, сильні сторони, наукові ризики | — |
| [01_REVIEW_document_sdom.md](01_REVIEW_document_sdom.md) | `src/document/` — SDOM ядро; зламаний frozen-контракт TEIDocument | 8/10 |
| [02_REVIEW_ingestion_extraction.md](02_REVIEW_ingestion_extraction.md) | `ingestion/` + `extraction/` — NSE-баг, одиниці, judge без ground truth, lxml/fitz | 5.5/10 |
| [03_REVIEW_orchestration.md](03_REVIEW_orchestration.md) | `orchestration/` + `actors/` + `registry/` — немає retry/health-checks | 6.5/10 |
| [04_REVIEW_graph_layer.md](04_REVIEW_graph_layer.md) | `graph/` + `graphstore/` — дублікат writer з CREATE, hardcoded паролі | 6/10 |
| [05_REVIEW_normalization_ontology_enrichment.md](05_REVIEW_normalization_ontology_enrichment.md) | контракти добрі, merge_ontology без тестів | 6.5/10 |
| [06_REVIEW_semantic_analytics_evaluation.md](06_REVIEW_semantic_analytics_evaluation.md) | semantic_objects добрі; analytics ковтає помилки | 7/10 |
| [07_REVIEW_dashboard_misc.md](07_REVIEW_dashboard_misc.md) | dashboard (55 мовчазних except), config, 5 stub-пакетів | 4/10 |
| [08_TESTING_AUDIT.md](08_TESTING_AUDIT.md) | покриття ~10% файлів; інфраструктура 9/10, покриття 3/10 | 3/10 |
| [09_REMEDIATION_PLAN.md](09_REMEDIATION_PLAN.md) | 5 фаз виправлень, ~4–6 тижнів, критерії завершення | план |
| [10_TRAINING_DATASET_PLAN.md](10_TRAINING_DATASET_PLAN.md) | 8 task-сімейств, 50–150K прикладів, JSONL-схема, ліцензії, `src/training/` | план |

## Швидка орієнтація

- **Найкритичніше для науки**: NSE/Kappa діапазони ([02](02_REVIEW_ingestion_extraction.md) F-EXT-1) → Фаза 2 плану.
- **Найкритичніше для архітектури**: TEIDocument не frozen ([01](01_REVIEW_document_sdom.md) F-DOC-1) → Фаза 0.4.
- **Найкритичніше для безпеки**: hardcoded credentials ([04](04_REVIEW_graph_layer.md) F-GR-2) → Фаза 0.1–0.3.
- **Перед стартом датасету**: завершити Фазу 2 (наукова вірність), інакше помилки закріпляться у вагах моделі.

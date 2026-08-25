# Індекс документації GeoHydroAI v2

> Документація версії 2 — створена 2026-06-09 на основі аналізу поточного коду та архіву AUDIT_v1.

---

## Навігація

| Файл | Призначення |
|------|-------------|
| [README.md](README.md) | Короткий опис проєкту, запуск, технологічний стек |
| [ARCHITECTURE.md](ARCHITECTURE.md) | Повна архітектура системи, компоненти, шари, діаграми |
| [PIPELINE.md](PIPELINE.md) | Детальний опис двох паралельних пайплайнів обробки |
| [KNOWLEDGE_GRAPH.md](KNOWLEDGE_GRAPH.md) | Neo4j граф знань: вузли, зв'язки, схема, аналітичні запити |
| [DATA_MODEL.md](DATA_MODEL.md) | Модель даних: paper.json, Parquet, ChromaDB, DuckDB |
| [CODEMAP.md](CODEMAP.md) | Карта коду: дерево проєкту, файли, залежності |
| [RESEARCH_WORKFLOW.md](RESEARCH_WORKFLOW.md) | Workflow дослідника, практичні сценарії, розширення |
| [AUDIT_HISTORY.md](AUDIT_HISTORY.md) | Аналіз AUDIT_v1: еволюція v1→v5, виправлення, відкриті проблеми |

---

## Швидка орієнтація

### Я хочу запустити pipeline
→ [README.md → Як запустити](README.md) + [PIPELINE.md → Команди](PIPELINE.md)

### Я хочу зрозуміти архітектуру
→ [ARCHITECTURE.md](ARCHITECTURE.md)

### Я хочу зрозуміти граф знань
→ [KNOWLEDGE_GRAPH.md](KNOWLEDGE_GRAPH.md)

### Я хочу знайти конкретний файл
→ [CODEMAP.md](CODEMAP.md)

### Я хочу зрозуміти, що було виправлено і що ще не вирішено
→ [AUDIT_HISTORY.md](AUDIT_HISTORY.md)

### Я хочу написати наукову статтю за допомогою цієї системи
→ [RESEARCH_WORKFLOW.md](RESEARCH_WORKFLOW.md)

---

## Поточний стан системи (станом на 2026-06-09)

| Компонент | Стан |
|-----------|------|
| GROBID TEI XML | 6,014 файлів |
| paper.json (Legacy Pipeline) | ~4,850 файлів |
| ChromaDB (768-dim SPECTER2) | 986,832 чанки / 3,686 papers |
| Neo4j | 57,603 вузли / 92,003 CITES |
| NumericFacts | 16,308 фактів / 667 papers |
| Нормалізовані papers | ~3,680 файлів |
| OpenAlex збагачення | 2,963 / 3,680 |
| NougatRegionPipeline | 180 / 3,875 papers |
| Tests | 606 (pass, no GPU required) |
| Pipeline maturity score | ~7.5/10 (post ChromaDB rebuild) |

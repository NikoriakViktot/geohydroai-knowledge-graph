# Equation-grounded scientific knowledge graph — план (узгоджено 2026-10-05)

Робоча назва: **provenance-aware, equation-centric knowledge graph for scientific literature**.
Внесок: відтворювані машинні ланцюжки
`concept → quantity → physical law → equation variant → parameter → value → method/model → metric → reported result → table/equation/page → PDF`,
де кожна ланка прив'язана до місця в документі.

Новизна — не «граф рівнянь» (AM-MKG 2026 вже має equation/variable/assumption), а цілісний
автоматичний ланцюжок рівняння ↔ величина ↔ метод ↔ метрика ↔ значення ↔ доказ, і
фізично-інформована перевірка видобутого (розмірності).

## Порядок робіт

**Gold set → Quantity ontology → EXACT → ALGEBRAIC → PHYSICAL LAW → API/MCP → corpus-scale evaluation.**
Реєстр законів починається з 15–20 найчастіших, не з 50.

## Модель даних

| Об'єкт | Поля / зв'язки | Правило |
|---|---|---|
| EquationOccurrence | raw_latex, text_grobid, canonical_expression, formula_text_hash, formula_structural_hash, page, bbox, PNG, png_sha256 | оригінал ніколи не замінюється канонічною формою |
| Parameter | symbol, description, unit, value, source, param_hash | прив'язаний до символу, що є в рівнянні |
| Quantity | canonical id, label, aliases, dimension {L,M,T,Θ,N,I,J}, typical units | розмірність — обов'язкова |
| PhysicalLaw | id, signature (набір величин, ліва частина), текстові й математичні ознаки | створюється з реєстру, не LLM |
| (Equation)-[:EQUATION_INSTANCE_OF {score, evidence}]->(PhysicalLaw) | score = w1·S_quantity + w2·S_math + w3·S_text + w4·S_method | LLM — лише один канал доказів |
| (Equation)-[:COMPUTES {computation_role}]->(Quantity) | computation_role ∈ computes_quantity, defines_metric, estimates_parameter, converts_quantity, relates_quantities | роль, а не нові типи ребер |
| (Equation)-[:EQUIVALENT {level}]->(Equation) | level ∈ EXACT, ALGEBRAIC, SAME_LAW | |

Еквівалентність:
- **FORMULA_TEXT_HASH** — LaTeX без форматування, нормалізовані пробіли й команди.
- **FORMULA_STRUCTURAL_HASH** — розбір → AST → канонічний AST (`\frac{Q}{A}`, `Q/A`, `{Q \over A}` → один хеш).
- **ALGEBRAIC** — лише серед кандидатів: однакова сигнатура величин → однакова розмірна
  сигнатура → схожа кількість змінних → схожий AST → перевірка SymPy (з точністю до знака/множника,
  розв'язок відносно кожної змінної). Q=AV ↔ V=Q/A потрапляє в кандидати, P=IV — ні.
- **SAME_PHYSICAL_LAW** — оцінка доказів (вище) проти реєстру законів.

Фізична перевірка: розмірність величини × заявлена одиниця → `DIMENSION_MISMATCH`
(Q з одиницею m/s → очікувано L³T⁻¹, отримано LT⁻¹). Окремий результат для статті:
physics-informed validation of extracted scientific knowledge.

## Еталонний набір (gold set v1)

Розмічає лише людина (R-ACC-7), через шар перевірки (`verify.human_check`), заморожується
з хешами після завершення корпусного прогону Nougat. Стратифікація: видавець, джерело
формули (LaTeX Nougat / TEI), тематика, наявність параметрів.

| Набір | Розмір | Мітки |
|---|---|---|
| equation | 300 рівнянь | формула вірна; COMPUTES-величина і computation_role |
| parameter | ~800 параметрів цих рівнянь | символ ↔ визначення, одиниця, значення |
| quantity_name | 200 поверхневих назв | канонічна Quantity |
| equation_pair | 150 пар: 50 еквівалентних, 50 очевидно різних, 50 складних | EXACT / ALGEBRAIC / SAME_LAW / RELATED / DIFFERENT / UNCERTAIN |
| metric_fact | 150 фактів | метрика → значення → таблиця/сторінка |
| qa_item | 30–50 питань | правильна відповідь і докази (готує людина) |

Складні негативи: Q=AV vs V=Q/A; Manning vs modified Manning; NSE vs normalized RMSE;
Darcy-Weisbach vs Manning; варіанти Green-Ampt.

## Оцінка (п'ять задач)

1. **Extraction** — equation, parameter, unit, metric, value: P/R/F1.
2. **Linking** — symbol→definition, equation→quantity, metric→value, value→table: edge P/R/F1.
3. **Normalization** — surface form → canonical Quantity: accuracy, macro-F1.
4. **Reasoning** — algebraic equivalence, physical-law identity: accuracy/F1 окремо.
5. **End-to-end QA** — vector RAG vs KG only vs KG + vector RAG: answer correctness,
   citation correctness, evidence completeness.

Абляції: GROBID only; Nougat по кропах; Nougat по сторінках зі шлюзом; локальні визначення
vs локальні + глосарій. Втрати по ланцюжку PDF → Equation → Parameter → Quantity → Law.

## Етапи

| # | Етап | Залежить від |
|---|---|---|
| 0 | Gold set: типи цілей у verify, семплер, сторінка розмітки, заморожування | кінець корпусного прогону |
| 1 | Quantity ontology з розмірностями: кластери назв → канонічні id → перевірка людиною; DIMENSION_MISMATCH | 0 (вибірка назв) |
| 2 | TEXT_HASH + STRUCTURAL_HASH, EquationOccurrence з raw/canonical | — |
| 3 | ALGEBRAIC через candidate generation | 1, 2 |
| 4 | Реєстр 15–20 законів, EQUATION_INSTANCE_OF з оцінкою доказів | 1, 3 |
| 5 | API/MCP: equations, quantities, laws, chains | 1–4 |
| 6 | Оцінка на gold set, QA-експеримент, стаття | 0–5 |

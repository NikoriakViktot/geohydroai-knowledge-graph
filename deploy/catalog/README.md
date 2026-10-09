# Сервер-каталог: розгортання на `geoai`

**Гілка**: `deploy/catalog-server`.

**Що це.** Читальний API карток праць: посилання + короткий відфільтрований аналіз. Аргументація — у [API_PLAN_v1/12_SERVER_MIGRATION_v2.md](../../API_PLAN_v1/12_SERVER_MIGRATION_v2.md), інструкція для ІІ-агентів — у [docs/catalog/AGENT_GUIDE.md](../../docs/catalog/AGENT_GUIDE.md).

**Сервер.** `geoai` (AWS, Ubuntu 24.04, 2 vCPU, 7,8 GB RAM; SSH-аліас `geoai`, користувач `ubuntu` у групі `docker`). На ньому вже працює `geoai-nginx` (80/443, Let's Encrypt для `geohydroai.org`). Каталог вбудовується в цю схему так само, як `platform-api`:
- контейнер `catalog-api` у docker-мережі `geoai_web`, **без власних портів**;
- `location /catalog/` у nginx.

```text
Робоча станція (фабрика)                           Сервер geoai
python -m src.catalog.build                        ~/geoai-catalog/
  → data/exports/catalog_<дата>_<правила>/    ──▶    data/releases/<знімок>/   cards.jsonl, manifest.json…
deploy/catalog/publish_catalog.sh (rsync+sha256)     data/current → releases/<знімок>
                                                     app/          git clone цієї гілки
                                                     catalog.env   хеші API-ключів (600)
                                                   catalog-api (docker, geoai_web) :8095
                                                   geoai-nginx  https://geohydroai.org/catalog/ → catalog-api
```

**Сервер не має:**
- PDF, повних текстів, анотацій;
- Postgres, ChromaDB, Neo4j, моделей.

**Ресурси:** образ ≈ 145 MB, знімок ≈ 12 MB. Контейнер обмежено 768 MB і 1 CPU; фактично він займає ≈ 260 MB при двох воркерах.

---

## 1. Перше розгортання

```bash
# сервер
ssh geoai
mkdir -p ~/geoai-catalog/data/releases
git clone --branch deploy/catalog-server --depth 1 \
    git@github.com:NikoriakViktot/geohydroai-knowledge-graph.git ~/geoai-catalog/app
install -m 600 /dev/null ~/geoai-catalog/catalog.env      # ключі — див. §2

# робоча станція: перший знімок (контейнера ще немає — скрипт лише покладе файли)
deploy/catalog/publish_catalog.sh data/exports/catalog_20261009_catalog-rules-v1 geoai

# сервер: образ і контейнер
cd ~/geoai-catalog/app
docker compose -f deploy/catalog/docker-compose.yml up -d --build
docker exec geoai-nginx curl -s http://catalog-api:8095/health      # {"status":"ok","cards":4742,…}
```

### nginx (наявний `geoai-nginx`)

1. Вставте [`nginx-catalog.conf`](nginx-catalog.conf) у блок `listen 443` домену `geohydroai.org` у `~/geoai/services/nginx/nginx.conf`. Це репозиторій `GeoHydroAI-V2`; закомітьте зміну там.
2. Перевірте конфіг і перезавантажте nginx без простою:

```bash
docker exec geoai-nginx nginx -t && docker exec geoai-nginx nginx -s reload
curl -s https://geohydroai.org/catalog/health
```

Апстрім задано змінною, а `resolver 127.0.0.11` уже є, тому nginx стартує, навіть коли `catalog-api` зупинено. Тоді `/catalog/` повертає 502.

---

## 2. API-ключі

Каталог публічний (`https://geohydroai.org/catalog/`), тому ключі **обов'язкові**. Сервер зберігає лише sha256 ключів.

```bash
# робоча станція: новий ключ на кожного споживача
KEY=$(python3 -c "import secrets; print(secrets.token_urlsafe(32))")
python3 -c "import hashlib,sys; print(hashlib.sha256(sys.argv[1].encode()).hexdigest())" "$KEY"
# ключ — споживачеві (один раз), хеш — на сервер:
#   ~/geoai-catalog/catalog.env:  CATALOG_API_KEY_HASHES=<sha256_1>,<sha256_2>
ssh geoai 'cd ~/geoai-catalog/app && docker compose -f deploy/catalog/docker-compose.yml up -d'   # перечитати env
```

Щоб відкликати ключ, приберіть його хеш і перезапустіть контейнер. Без ключа відкриті лише `/health`, `/agent-guide`, `/llms.txt`, `/docs`, `/openapi.json`.

---

## 3. Новий знімок

```bash
cd ~/projects/knoweledg_graf
set -a; . ./.env; set +a                      # GHAI_PG_* — збирання читає Postgres
python -m src.catalog.build                   # → data/exports/catalog_<дата>_<правила>/
deploy/catalog/publish_catalog.sh data/exports/<знімок> geoai
```

**Що робить скрипт:**
1. rsync у `releases/<ім'я>.part/`;
2. перевірка sha256 кожного файлу за `manifest.json` на сервері;
3. перейменування на `releases/<ім'я>/`;
4. атомарне перемикання `data/current`;
5. `docker restart catalog-api`;
6. перевірка `/health`.

Те саме ім'я двічі не публікується.

API під час старту сам перевіряє sha256 `cards.jsonl`. Пошкоджений знімок він не обслуговуватиме: контейнер не стане healthy, і скрипт про це скаже.

**Відкат**: `deploy/catalog/publish_catalog.sh --rollback geoai`.

**Оновлення коду API:**

```bash
ssh geoai 'cd ~/geoai-catalog/app && git pull --ff-only && docker compose -f deploy/catalog/docker-compose.yml up -d --build'
```

---

## 4. Перевірка

```bash
curl -s https://geohydroai.org/catalog/health
curl -s https://geohydroai.org/catalog/agent-guide | head -3
curl -s -o /dev/null -w '%{http_code}\n' https://geohydroai.org/catalog/v1/cards     # 401
curl -s -H "X-API-Key: $CATALOG_API_KEY" https://geohydroai.org/catalog/v1/release  # cards_sha256 = manifest.json
ssh geoai docker logs --tail 50 catalog-api
```

---

## 5. Бекапи й прибирання

- **Відновлюване.** Усе на сервері відновлюється з робочої станції: знімки лежать у `data/exports/`, код — у git.
- **Що зберігати:** лише `~/geoai-catalog/catalog.env`.
- **Старі релізи** (лишити 3):

```bash
ssh geoai 'cd ~/geoai-catalog/data && ls -1d releases/*/ | sort | head -n -3 | xargs -r rm -rf'
```

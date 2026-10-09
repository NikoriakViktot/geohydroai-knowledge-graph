# Сервер-каталог: розгортання

**Гілка**: `deploy/catalog-server`.

**Що це.** На сервері працює лише читальний API карток праць: посилання + короткий відфільтрований аналіз. Аргументація — у [API_PLAN_v1/12_SERVER_MIGRATION_v2.md](../../API_PLAN_v1/12_SERVER_MIGRATION_v2.md), інструкція для ІІ-агентів — у [docs/catalog/AGENT_GUIDE.md](../../docs/catalog/AGENT_GUIDE.md).

```text
Робоча станція (фабрика)                          Сервер (вітрина)
python -m src.catalog.build                       /srv/catalog/
  → data/exports/catalog_<дата>_<правила>/   ──▶    releases/<знімок>/   (cards.jsonl, manifest.json, …)
deploy/catalog/publish_catalog.sh  (rsync+sha)      current → releases/<знімок>
                                                    app/   (git clone цієї гілки)
                                                    venv/  (fastapi + uvicorn)
                                                  systemd catalog-api → 127.0.0.1:8095
                                                  Caddy (HTTPS) або Tailscale
```

**Сервер не має:**
- PDF, повних текстів, анотацій;
- Postgres, ChromaDB, Neo4j, моделей, GPU.

**Ресурси:** 1 vCPU, 1–2 GB RAM, 10 GB диска. Один знімок важить ≈ 12 MB. API займає ≈ 130 MB пам'яті на воркер, тобто ≈ 260 MB при двох воркерах (виміряно 2026-10-09).

---

## 1. Сервер: одноразове налаштування (Ubuntu 24.04)

```bash
# від root або через sudo
apt update && apt install -y python3-venv git curl rsync ufw
adduser --system --group --home /srv/catalog catalog
install -d -o catalog -g catalog /srv/catalog/releases
# код: лише ця гілка, без історії
sudo -u catalog git clone --branch deploy/catalog-server --depth 1 \
     git@github.com:NikoriakViktot/geohydroai-knowledge-graph.git /srv/catalog/app
sudo -u catalog python3 -m venv /srv/catalog/venv
sudo -u catalog /srv/catalog/venv/bin/pip install -r /srv/catalog/app/deploy/catalog/requirements.txt
```

Репозиторій приватний, тож потрібен deploy key сервера в GitHub (*Settings → Deploy keys*, лише читання). Альтернатива без доступу до GitHub — скопіювати теку `src/catalog` і `docs/catalog` через `rsync`, бо API потрібні лише вони.

**systemd:**

```bash
cp /srv/catalog/app/deploy/catalog/catalog-api.service /etc/systemd/system/
systemctl daemon-reload && systemctl enable catalog-api
# сервіс стартує після першої публікації (потрібен /srv/catalog/current)
```

**Право на перезапуск для скрипта публікації.** Користувач, під яким заходить `publish_catalog.sh`, має мати лише цей дозвіл:

```bash
echo 'catalog ALL=(root) NOPASSWD: /usr/bin/systemctl restart catalog-api' > /etc/sudoers.d/catalog-api
chmod 440 /etc/sudoers.d/catalog-api
```

Для цього ж користувача додайте SSH-ключ робочої станції в `/srv/catalog/.ssh/authorized_keys`.

**Фаєрвол:**

```bash
ufw default deny incoming && ufw allow OpenSSH
ufw allow 443/tcp          # лише для варіанта 3а (публічний HTTPS)
ufw enable
```

---

## 2. Доступ

### 2.1 Ключі API

Рекомендовано навіть у приватній мережі. Сервер зберігає **лише sha256** ключів, самі ключі — ні.

```bash
# на робочій станції: створити ключ для кожного споживача
KEY=$(python3 -c "import secrets; print(secrets.token_urlsafe(32))")
echo "$KEY"                                             # передати споживачеві один раз
python3 -c "import hashlib,sys; print(hashlib.sha256(sys.argv[1].encode()).hexdigest())" "$KEY"
```

```bash
# на сервері: /srv/catalog/catalog.env (власник root, режим 600), хеші через кому
CATALOG_API_KEY_HASHES=<sha256_1>,<sha256_2>
```

Після зміни файлу виконайте `systemctl restart catalog-api`. Щоб відкликати ключ, приберіть його хеш.

### 2.2 Мережа — оберіть одне

- **а) Публічний HTTPS.** Встановіть Caddy (`apt install caddy`), скопіюйте `Caddyfile.example` у `/etc/caddy/Caddyfile` і замініть домен. Сертифікат Caddy отримає сам. Ключі API тут **обов'язкові**.
- **б) Tailscale (приватно).** Встановіть Tailscale (`curl -fsSL https://tailscale.com/install.sh | sh && tailscale up`). В юніті змініть `--host 127.0.0.1` на tailnet-IP сервера, або виконайте `tailscale serve --bg 8095`. Порт 443 назовні не відкривається.

---

## 3. Публікація знімка (на робочій станції)

```bash
cd ~/projects/knoweledg_graf
set -a; . ./.env; set +a                         # GHAI_PG_* для читання Postgres
python -m src.catalog.build                      # → data/exports/catalog_<дата>_<правила>/
deploy/catalog/publish_catalog.sh data/exports/catalog_20261009_catalog-rules-v1 catalog@<server>
```

**Що робить скрипт:**
1. rsync у `releases/<ім'я>.part/`;
2. перевірка sha256 кожного файлу за `manifest.json` на сервері;
3. перейменування на `releases/<ім'я>/`;
4. атомарне перемикання `current`;
5. перезапуск сервісу і перевірка `/health`.

Те саме ім'я релізу двічі не публікується.

API також сам перевіряє sha256 `cards.jsonl` під час старту. Пошкоджений або змінений вручну знімок він не обслуговуватиме.

**Відкат на попередній реліз:**

```bash
deploy/catalog/publish_catalog.sh --rollback catalog@<server>
```

**Оновлення коду API** (рідко; знімки від цього не залежать):

```bash
sudo -u catalog git -C /srv/catalog/app pull --ff-only
systemctl restart catalog-api
```

---

## 4. Перевірка після розгортання

```bash
curl -s https://<host>/health                                    # {"status":"ok","cards":4742,…}
curl -s https://<host>/agent-guide | head                        # інструкція для агентів
curl -s -H "X-API-Key: $KEY" https://<host>/v1/release            # rules_version, cards_sha256
curl -s -H "X-API-Key: $KEY" "https://<host>/v1/cards?q=kakhovka" | head -c 400
curl -s -o /dev/null -w '%{http_code}\n' https://<host>/v1/cards  # 401, якщо ключі ввімкнено
```

- `cards_sha256` у `/v1/release` має збігтися з `manifest.json` локального знімка.
- **Журнали:** `journalctl -u catalog-api -n 100`.

---

## 5. Бекапи

- **Відновлюване.** Сервер не містить нічого, що не можна відновити: знімки лежать у `data/exports/` на робочій станції, код — у git.
- **Що зберігати на сервері:** лише `/srv/catalog/catalog.env` (хеші ключів) і, за бажання, 3 останні релізи.
- **Прибирання старих релізів:** `ls -1d /srv/catalog/releases/*/ | sort | head -n -3 | xargs rm -rf`.

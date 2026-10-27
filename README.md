# LCALauncher Auth Server & Modpack Backend

Асинхронный бэкенд на Python для авторизации и синхронизации сборок Minecraft в кастомном лаунчере **LCALauncher** (C++/Qt 6).

Включает:
1. **Telegram-бот (aiogram 3.x)** — регистрация игровых ников, привязка лаунчера по одноразовому 6-значному цифровому коду, управление профилем и сессиями, модерация, загрузка модов и управление сборкой для администраторов.
2. **HTTP REST API (aiohttp, порт 8080)** — обслуживание запросов лаунчера:
   - Запрос одноразового кода и поллинг авторизации (`/api/auth/request-code`, `/api/auth/poll/{code}`).
   - Восстановление и валидация сессий по JWT (`/api/auth/validate`, `/api/auth/logout`).
   - Синхронизация сборки (SyncEngine): манифест модов (`/api/modpack/manifest.json`), отдача файлов (`/api/modpack/files/{path}`), защищённый аплоад и управление версиями.
   - **Yggdrasil Session Server для сервера Minecraft**: эндпоинты `join`, `hasJoined`, `profile`, совместимые со стандартом Mojang и `authlib-injector`.
   - **Сервер скинов и плащей**: отдача текстур по SHA-256 (`/textures/{hash}`), RSA PKCS#1 v1.5 подпись текстур.
3. **База данных (SQLAlchemy 2.0 Async + aiosqlite)** — хранение пользователей, офлайн UUID v3, кодов привязки, активных JWT-сессий, скинов, плащей и файлов сборки.

---

## 🚀 Быстрый старт

### 1. Требования
- Python 3.11+
- Virtualenv

### 2. Установка зависимостей
```bash
python3 -m venv .venv
source .venv/bin/activate
pip install -r requirements.txt
```

### 3. Конфигурация (.env)
Скопируйте шаблон переменных окружения:
```bash
cp .env.example .env
```
Отредактируйте `.env`:
```env
BOT_TOKEN=123456789:ABCDEFghijklmnopqrstuvwxyz1234567
JWT_SECRET=generate-a-strong-secret-key
ADMIN_API_TOKEN=super-secret-admin-token
ADMIN_TELEGRAM_IDS=123456789
API_BASE_URL=http://localhost:8080
```

### 4. Запуск сервера и бота
```bash
python3 -m bot
```

Сервер автоматически:
- Создаст базу данных SQLite и таблицы.
- Запустит HTTP REST API на `http://0.0.0.0:8080`.
- Запустит фоновую задачу периодической очистки истёкших кодов (каждые 10 минут).
- Запустит Telegram-бота через aiogram polling.

---

## 🧪 Запуск тестов

Проект покрыт автоматическими тестами (pytest, pytest-asyncio):
```bash
pytest -v
```

Тесты проверяют:
- Генерацию Minecraft offline UUID v3 (совместимо с алгоритмом лаунчера).
- Создание и проверку JWT-токенов.
- Регистрацию ников и валидацию (формат, уникальность).
- Генерацию 6-значных одноразовых кодов, подтверждение через бота, создание сессий.
- Защиту от rate limiting (API + бот).
- Защиту от Path Traversal при работе с файлами сборки.
- HTTP REST API (request-code, poll, validate, logout, manifest, upload, delete, version).

---

## 📡 HTTP API для лаунчера

### Авторизация

#### `POST /api/auth/request-code`
Запрос 6-значного кода лаунчером.
- **Ограничение:** не более 10 запросов в минуту с одного IP.
- **Ответ (200 OK):**
```json
{
  "code": "482917",
  "expires_in": 300
}
```

#### `GET /api/auth/poll/{code}`
Поллинг статуса подтверждения кода (каждые 2-3 сек).
- **Ожидание ввода (200 OK):**
```json
{"status": "pending"}
```
- **Успех (200 OK):**
```json
{
  "status": "confirmed",
  "username": "PlayerNick",
  "uuid": "5627dd98-e6be-3c21-b8a8-e92344183641",
  "accessToken": "eyJhbGciOiJIUzI1NiIs..."
}
```
- **Истёк (200 OK):**
```json
{"status": "expired"}
```
- **Код не найден (404 Not Found):**
```json
{"error": "invalid_code", "errorMessage": "Код не найден"}
```

#### `GET /api/auth/validate`
Проверка токена лаунчера при запуске.
- **Заголовок:** `Authorization: Bearer <accessToken>`
- **Ответ (200 OK):**
```json
{"valid": true, "username": "PlayerNick", "uuid": "..."}
```
- **Ответ (401 Unauthorized):**
```json
{"valid": false, "error": "token_expired"}
```

#### `POST /api/auth/logout`
Отзыв сессии в лаунчере.
- **Заголовок:** `Authorization: Bearer <accessToken>`
- **Ответ (200 OK):**
```json
{"success": true}
```

---

### Сборка / Модпак (SyncEngine)

#### `GET /api/modpack/manifest.json`
Отдаёт текущий манифест сборки для лаунчера:
```json
{
  "version": "1.2.0",
  "files": [
    {
      "path": "mods/SomeMod-1.0.jar",
      "url": "http://server/api/modpack/files/mods/SomeMod-1.0.jar",
      "sha256": "abc12345...",
      "size": 1048576
    }
  ]
}
```

#### `GET /api/modpack/files/{path:.*}`
Скачивание бинарного файла сборки лаунчером (`Content-Type: application/octet-stream`).

#### `POST /api/modpack/upload` (Админский)
Загрузка файла в сборку.
- **Заголовок:** `X-Admin-Token: <admin_api_token>`
- **Тело (multipart/form-data):** `file`, `path`
- **Ответ (200 OK):**
```json
{
  "success": true,
  "path": "mods/NewMod-2.0.jar",
  "sha256": "...",
  "size": 1048576
}
```

#### `DELETE /api/modpack/files/{path:.*}` (Админский)
Удаление файла из сборки и с диска.
- **Заголовок:** `X-Admin-Token: <admin_api_token>`

#### `POST /api/modpack/version` (Админский)
Обновление версии сборки и changelog:
- **Заголовок:** `X-Admin-Token: <admin_api_token>`
- **Тело (JSON):** `{"version": "1.3.0", "changelog": "Добавлены моды"}`

---

### Yggdrasil Session Server (Авторизация сервера Minecraft и Скины)

Бэкенд реализует официальный протокол **Mojang Yggdrasil Session Server** с автоматической генерацией 4096-битной RSA-пары ключей (`data/keys/`) и подписью текстур SHA1withRSA:

#### 1. Подключение сервера Minecraft (Paper / Spigot / Fabric / Forge)
Сервер Minecraft запускается с `online-mode=true` с параметром JVM агента [authlib-injector](https://github.com/yushijinhun/authlib-injector):
```bash
java -javaagent:authlib-injector.jar=http://<ip-бэкенда>:8080/api/yggdrasil -jar server.jar nogui
```
*В таком режиме сервер Minecraft полностью защищен от подделки ников, а скины и плащи отображаются у всех игроков.*

#### 2. Эндпоинты протокола Yggdrasil
- `GET /api/yggdrasil` — метаданные для authlib-injector (имя сервера, публичный RSA ключ, разрешенные домены скинов).
- `POST /sessionserver/session/minecraft/join` — игра подтверждает вход с полученным `accessToken` перед подключением к серверу.
- `GET /sessionserver/session/minecraft/hasJoined?username=...&serverId=...` — сервер Minecraft проверяет игрока и получает его профиль со скином и плащом.
- `GET /sessionserver/session/minecraft/profile/{uuid}` — получение профиля игрока по UUID с подписанными Base64 текстурами.
- `GET /textures/{hash}` — отдача PNG файла текстуры скина или плаща.
- `GET /api/skins/{username}.png` — прямая ссылка на скин игрока.
- `GET /api/capes/{username}.png` — прямая ссылка на плащ игрока.

---

## 🤖 Команды Telegram-бота

### Для игроков:
- `/start` — приветствие, предложение регистрации или профиль с инструкцией.
- `[6-значный код]` — ввод кода из лаунчера для мгновенного входа.
- `/profile` — просмотр ника, UUID, статуса, смена ника, переход к управлению скином и плащом, кнопка завершения всех сессий.
- `/skin` — меню управления скином: загрузка `.png` (64x64 или 64x32), переключение типа рук (Classic Steve 4px / Slim Alex 3px), сброс на стандартный.
- `/cape` — меню управления плащом: загрузка плаща `.png` (64x32), удаление плаща.
- `/sessions` — просмотр списка активных сессий лаунчера с возможностью отозвать отдельные устройства.
- `/help` — подробная инструкция по входу.

### Для администраторов (из `ADMIN_TELEGRAM_IDS`):
- `/upload` — пошаговая загрузка любого мода/конфига документом в сборку.
- `/delete_mod` — интерактивный список файлов сборки с кнопками подтверждения удаления.
- `/modpack_info` — текущая версия, количество файлов, общий размер сборки и дата обновления.
- `/set_version` — обновление версии сборки и чейнджлога.
- `/ban <ник_или_tg_id>` — блокировка игрока с автоматическим отзывом всех сессий.
- `/unban <ник_или_tg_id>` — разблокировка игрока.
- `/userinfo <ник_или_tg_id>` — детальная информация об игроке и его сессиях.

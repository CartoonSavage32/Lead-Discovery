# Website Leads Discovery

Continuously discovers local businesses by **country × city × industry**, audits public websites with the BeaverCheck API, scores website-build and website-fix opportunities, and emails a daily CSV to Telegram.

## What this does not do

- It does **not** scrape Google Maps results or persist Maps HTML.
- It does **not** bypass CAPTCHA, logins, or anti-bot controls.
- Google Maps is used only to **build search URLs** of the form:

  `https://www.google.com/maps/search/?api=1&query=<industry>+<city>+<country>`

Discovery itself uses **OpenStreetMap** (Nominatim + Overpass), which is a permitted public dataset. Swap the provider without touching scoring, audit, contacts, or reports.

Website audits use only the BeaverCheck public API (`POST /api/v2/batch`). The app never submits scans, never calls `/submit`, and never treats a missing public audit as a bad website. Audit payloads are not written to disk.

## Stack

Python 3.13, Playwright, httpx, Pydantic v2, BeautifulSoup4, PyYAML, python-dotenv, pytest, Ruff, Pyright.

No database. State is `data/state.json` (atomic writes) plus `data/reports/`.

## Setup

```bash
python -m venv .venv
.venv\Scripts\activate
pip install -r requirements.txt
playwright install chromium
copy .env.example .env
```

Set Telegram values in `.env`:

```
TELEGRAM_BOT_TOKEN=...
TELEGRAM_CHAT_ID=...
```

Edit `config.yaml`, `data/countries.yaml`, `data/cities.yaml`, and `data/industries.yaml`. Cities are scoped to a country. Industries carry OSM tags used only by the OSM provider.

## Commands

```bash
python -m app run            # continuous loop + daily report at 20:30 Asia/Kolkata
python -m app discover       # one unprocessed combination
python -m app scan           # audit, score, and contact-extract known businesses
python -m app report         # write CSV and send Telegram
python -m app test-telegram  # send a test message
```

## Daily report

At `report_time` in `timezone` (default 20:30 Asia/Kolkata) the runner writes a CSV sorted by opportunity score and sends it to Telegram with:

```
Website leads: X
No-website leads: Y
Top opportunity: BUSINESS - SCORE
```

## Docker

Persistent volume is `/app/data`:

```bash
docker compose up --build
```

## Tests

```bash
pytest
ruff check app tests
pyright
```

## Replacing discovery

Set `discovery.provider` to `file` and `discovery.file_path` to a JSON list of business records, or implement another class with `async def discover(combination) -> list[BusinessRecord]` and register it in `app/discovery/factory.py`.

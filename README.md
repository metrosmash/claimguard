# ClaimGuard

ClaimGuard is a demo claims auditor for Nigerian HMO and hospital bills. It extracts bill details, compares prices with illustrative sample tariffs, checks items against the diagnosis, and calculates a deterministic fraud score.

> **Demo only:** sample bills and tariffs are synthetic, are not official NHIA tariffs, and contain no real patient data. ClaimGuard is not medical advice or a production claims adjudicator.

## How it works

1. **Extract** line items and bill details from an image with a vision model.
2. **Check prices** against `data/tariffs_sample.csv`.
3. **Review medical consistency** between the diagnosis and billed items.
4. **Score** rule-based flags such as overcharges, duplicates, unrelated items, and arithmetic mismatches.

The API is FastAPI; the web interface is React and Vite. The main audit endpoint is `POST /audit`.

## LLM providers

| Provider | Configuration | Use |
|---|---|---|
| Gemini API | `LLM_BASE_URL`, `LLM_API_KEY`, `LLM_MODEL` | Primary model; defaults to `gemma-4-31b-it` |
| Groq | `GROQ_API_KEY`, `GROQ_MODEL` | Automatic provider fallback; `qwen/qwen3.8-27b` supports vision and JSON mode |
| Ollama or vLLM | `LLM_FALLBACK_BASE_URL`, `LLM_FALLBACK_API_KEY`, `LLM_FALLBACK_MODEL` | Optional self-hosted fallback |

Copy `.env.example` to `.env` and set `FACTORY_MASTER_KEY`. Add provider credentials to enable model-backed auditing. JSON sample bills can be audited without an LLM; a matching JSON sidecar also enables rules-only processing for sample images when no primary or self-hosted model is configured.

See [docs/gemma.md](docs/gemma.md) for provider and hosting details.

## Local setup

### Windows PowerShell

```powershell
python -m venv .venv
.\.venv\Scripts\Activate.ps1
python -m pip install -r requirements.txt
Copy-Item .env.example .env
```

Edit `.env` and set a development `FACTORY_MASTER_KEY`. To run image extraction with Groq, set `GROQ_API_KEY` and `GROQ_MODEL=qwen/qwen3.8-27b`. To generate or refresh synthetic samples, run:

```powershell
python scripts/make_samples.py
```

Start the API:

```powershell
$env:PYTHONPATH = "."
uvicorn main:app --reload --port 8000
```

### macOS / Linux

```bash
python -m venv .venv
source .venv/bin/activate
python -m pip install -r requirements.txt
cp .env.example .env
python scripts/make_samples.py
export PYTHONPATH=.
uvicorn main:app --reload --port 8000
```

Set `FACTORY_MASTER_KEY` and any provider credentials in `.env` before starting the API.

## How to test

### Run the automated tests

From the repository root, run:

```powershell
python -m pytest -q
```

or:

```bash
python -m pytest -q
```

The tests exercise deterministic scoring, tariff matching, and API behavior without requiring live LLM credentials.

### Smoke-test the API

With the API running, use a second terminal. Set the same development key used by the server:

```powershell
$env:FACTORY_MASTER_KEY = "dev_key_change_me"
curl.exe http://127.0.0.1:8000/health
curl.exe -X POST http://127.0.0.1:8000/audit `
  -H "X-Factory-API-Key: $env:FACTORY_MASTER_KEY" `
  -F "file=@data/sample_bills/bill_padded.json"
```

The padded sample should return an audit report with fraud flags. Try `bill_clean.json` for a clean sample. To exercise image extraction, upload `bill_clean.jpg`; configure a vision-capable provider in `.env` for model-backed extraction.

### Run the web interface

Install Node.js, then start Vite:

```powershell
cd frontend
npm ci
npm run dev -- --host 127.0.0.1
```

Open the local URL printed by Vite. In the app's **Settings**, set the API host to `http://127.0.0.1:8000` and enter the same `FACTORY_MASTER_KEY` used by the API. The frontend's default development API host points to the hosted demo.

## Deployment

The backend and frontend deploy separately:

- **Render** builds the [Dockerfile](Dockerfile) and hosts the FastAPI API only (`/health`, `/audit`, …).
- **Vercel** builds and serves the React app from `frontend/`, proxying API calls to Render through `/cg-api/*`.

Deployment configuration and instructions:

- [Render Blueprint](render.yaml)
- [Deployment plan](docs/deployment.plan.md)
- [Gemma and provider hosting guide](docs/gemma.md)

## Project documentation

- [Architecture](docs/architecture.md)
- [Agent instructions and schemas](docs/agent.md)
- [Implementation plan](docs/implementation.plan.md)
- [Frontend README](frontend/README.md)

## License

[MIT](LICENSE)

# gemma.md — Hosting Gemma 4 for ClaimGuard (fast path first)

Sources (checked Oct 31, 2026): https://gemma.ai/deployment/gemini-api/ · https://gemma.ai/deployment/ollama/ · https://ollama.com/library/gemma4 · https://docs.vllm.ai/projects/recipes/en/stable/Google/Gemma4.html · https://docs.digitalocean.com/products/droplets/details/pricing/

## TL;DR decision
| Path | Time to working | Cost | Use for |
|---|---|---|---|
| **A. Gemini API `gemma-4-31b-it`** (PRIMARY) | 5 min | free tier (AI Studio key) | the live demo, always |
| **B. DigitalOcean GPU Droplet + Ollama `gemma4:12b`** | 20–30 min | RTX 4000 Ada 20 GB = **$0.76/h** → $25 ≈ 30 h | "self-hosted open weights" story |
| C. Google Colab T4 + Ollama `gemma4:e4b` + cloudflared | 20 min | free | fallback if DO GPU quota is blocked |

The app only switches 3 env vars (`LLM_BASE_URL`, `LLM_API_KEY`, `LLM_MODEL`) — everything speaks the OpenAI-compatible API.

Gemini API only hosts `gemma-4-31b-it` and `gemma-4-26b-a4b-it`. Ollama tags: `gemma4:e2b` (7.2 GB), `gemma4:e4b` (9.6 GB), `gemma4:12b` (7.6 GB), `gemma4:26b` (18 GB), `gemma4:31b` (20 GB). Ollama Gemma 4 = text + image (vision works for bills).

---
## A. Gemini API (do this first, 5 min)
1. https://aistudio.google.com/apikey → Create API key → copy (never paste in chat / never commit).
2. `.env`:
```
LLM_BASE_URL=https://generativelanguage.googleapis.com/v1beta/openai/
LLM_API_KEY=<your AI Studio key>
LLM_MODEL=gemma-4-31b-it
```
3. Health check (PowerShell):
```powershell
$k=$env:LLM_API_KEY
curl.exe -s https://generativelanguage.googleapis.com/v1beta/openai/chat/completions -H "Authorization: Bearer $k" -H "Content-Type: application/json" -d '{\"model\":\"gemma-4-31b-it\",\"messages\":[{\"role\":\"user\",\"content\":\"say OK\"}]}'
```
If the OpenAI-compat route rejects the Gemma model id, fall back to the native SDK (`pip install google-genai`; `genai.Client().models.generate_content(model="gemma-4-31b-it", contents=[...])`) — `core/llm.py` should support both (see architecture.md).

---
## B. DigitalOcean GPU Droplet + Ollama (open-weights story)
Pricing: RTX 4000 Ada (20 GB VRAM, 32 GiB RAM) **$0.76/h**; L40S 48 GB $1.57/h. Billed per second. **Powered-off droplets still bill — DESTROY when done.** Budget: run ~6 h on RTX 4000 = ~$4.60.

1. cloud.digitalocean.com → Create → GPU Droplets → **RTX 4000 Ada** → image "AI/ML Ready" (NVIDIA drivers preinstalled) → add SSH key (or password) → Create. (New accounts may need GPU access approval — if blocked, go to C.)
2. SSH in (PowerShell): `ssh root@<DROPLET_IP>`
3. Install + serve:
```bash
curl -fsSL https://ollama.com/install.sh | sh
systemctl stop ollama
export OLLAMA_HOST=0.0.0.0:11434 OLLAMA_CONTEXT_LENGTH=16000
nohup ollama serve > /root/ollama.log 2>&1 &
ollama pull gemma4:12b      # fits 20 GB; e4b if slow
nvidia-smi
```
4. Lock it down (Ollama has NO auth). Easiest: only allow your IP:
```bash
ufw allow 22 && ufw allow from <YOUR_PUBLIC_IP> to any port 11434 && ufw --force enable
```
   Or don't open the port at all and use a Cloudflare quick tunnel: `curl -L -o cf https://github.com/cloudflare/cloudflared/releases/latest/download/cloudflared-linux-amd64 && chmod +x cf && ./cf tunnel --url http://localhost:11434` (URL is unguessable but public — fine for a demo only).
5. `.env`:
```
LLM_BASE_URL=http://<DROPLET_IP>:11434/v1
LLM_API_KEY=ollama
LLM_MODEL=gemma4:12b
```
6. Health check: `curl.exe http://<DROPLET_IP>:11434/v1/models` then a chat call like in A.

Optional vLLM (needs bigger GPU for 26B; E4B wants 24 GB+ so RTX 4000 20 GB is too small for BF16 — stick with Ollama on RTX 4000):
`docker run --gpus all -p 8000:8000 vllm/vllm-openai:gemma4 --model google/gemma-4-E4B-it --max-model-len 16384` (L40S).

---
## C. Google Colab (free T4, 15 GB VRAM) fallback
New notebook → Runtime → T4 GPU. Cells:
```python
!curl -fsSL https://ollama.com/install.sh | sh
import subprocess, os
os.environ["OLLAMA_HOST"]="0.0.0.0:11434"
subprocess.Popen(["ollama","serve"])
!sleep 5 && ollama pull gemma4:e4b
!wget -q https://github.com/cloudflare/cloudflared/releases/latest/download/cloudflared-linux-amd64 -O cf && chmod +x cf
subprocess.Popen(["./cf","tunnel","--url","http://localhost:11434"], stderr=open("cf.log","w"))
!sleep 8 && grep -o 'https://[a-z0-9-]*\.trycloudflare.com' cf.log
```
`.env`: `LLM_BASE_URL=https://<xxx>.trycloudflare.com/v1`, `LLM_API_KEY=ollama`, `LLM_MODEL=gemma4:e4b`.
Caveats: session dies on idle/disconnect (keep tab open); URL changes each restart.

---
## Hosting the app itself
- Backend (FastAPI, API-only Dockerfile): Render, or DO **App Platform** → Create App → GitHub repo → Dockerfile detected → port 8000 → add env vars as *encrypted* → deploy (basic plan ~$5/mo, from the $25 credit). Or a $6 basic Droplet: `docker build -t claimguard . && docker run -d -p 80:8000 --env-file .env claimguard`. The container serves the API only; open the Vercel UI and point it at this backend.
- Frontend: Vercel (repo already set up for it) with `VITE_API_URL=/cg-api`; `frontend/vercel.json` proxies `/cg-api/*` to the Render backend.

## Troubleshooting
- `model not found` → `ollama list`; exact tag. Gemini: use `gemma-4-31b-it`.
- Slow / OOM → smaller tag (12b → e4b → e2b), lower `OLLAMA_CONTEXT_LENGTH`.
- Image ignored → send as OpenAI `image_url` with `data:image/jpeg;base64,...`; resize to ≤1280px first.
- JSON broken → `response_format={"type":"json_object"}` (Ollama supports `format: json`), plus a repair-retry in code.
- 429 on Gemini → fall back to self-hosted URL (`LLM_FALLBACK_*` vars).
- After demo: **destroy the GPU droplet**.

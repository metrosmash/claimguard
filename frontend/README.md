# ClaimGuard frontend

React + Vite dashboard: upload a bill → live agent steps → audit report.

```bash
npm install
npm run dev
```

Default API host: `https://claimguard-sfxx.onrender.com` in development (see `.env.development`). Production builds use `VITE_API_URL=/cg-api`, and `vercel.json` proxies `/cg-api/*` to the Render backend. Paste the same `FACTORY_MASTER_KEY` into the UI as `X-Factory-API-Key`.

## Deployment

Vercel builds and serves this directory; the Render Dockerfile is API-only. See [../docs/deployment.plan.md](../docs/deployment.plan.md).

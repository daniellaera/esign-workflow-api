# Public demo deployment

A Docker Compose stack for running the API in demo mode on a single host behind a TLS-terminating reverse proxy.

- `compose.demo.yml`: API and MongoDB with memory/CPU limits and `restart: unless-stopped`. MongoDB publishes no port; only the API's port 8000 is exposed.
- `demo.env.example`: the site-specific settings. Copy it to `demo.env` (never committed).
- `redeploy.sh`: pulls `origin/main` and rebuilds. Run it on the host:

```bash
/path/to/esign-workflow-api/deploy/redeploy.sh
```

Demo mode (`ESIGN_DEMO_MODE=true`) deletes all data after 24 h, limits uploads to 2 MB and envelopes to 5 signers, rate-limits each client IP, accepts a public API key, exposes `GET /envelopes/{id}/signing-link`, and sends no webhooks. Set `ESIGN_TRUSTED_PROXIES` to the reverse proxy's address so rate limiting sees real client IPs.

# Deployment Guide

## Prerequisites

- Docker and Docker Compose
- MongoDB
- Redis
- Production secrets for the AI, storage, and OTP providers

## Recommended Environment Files

- Copy `.env.example` to `.env`
- Store real production secrets outside the repository
- Use a separate `.env.production` for cloud hosts if your deployment process prefers explicit profiles

## Local Container Run

```bash
docker compose up --build
```

The app will be available through the Nginx container on port `80`.

## Direct App Run

```bash
pip install -r requirements.txt
python app.py
```

## Production Process Manager

Use `gunicorn.conf.py` with Gunicorn in production. The included `Dockerfile` already starts Gunicorn with that config.

## Health Checks

- Application health: `GET /api/v1/health`
- Proxy health: `GET /healthz`

## Backups

Recommended backup scope:

- MongoDB data
- Uploaded documents
- Generated letters
- Any cached vector index or regenerated RAG assets if you treat them as deployment state

Suggested routine:

1. Snapshot MongoDB regularly.
2. Archive uploaded files and generated letters.
3. Keep a copy of the environment file in a secure secret store, not in the repo.
4. Verify restore procedures in a staging environment.

## HTTPS

- Terminate TLS at your cloud load balancer or reverse proxy.
- Keep `SESSION_COOKIE_SECURE=true` and `JWT_COOKIE_SECURE=true` in production.
- Narrow `CORS_ALLOWED_ORIGINS` to the actual hostnames you serve.

## Cloud Deployment Notes

- Put the web container behind a managed load balancer.
- Run MongoDB and Redis as managed services if possible.
- Attach persistent volumes for uploads and generated documents.
- Use a secrets manager for API keys and JWT secrets.
- Point the reverse proxy at `/api/v1/health` for readiness checks.

## Recovery Checklist

- Confirm the MongoDB backup restores cleanly.
- Confirm the app boots with a fresh `.env`.
- Confirm claim upload, review, and admin dashboards render after deployment.
- Confirm the CI workflow passes before promoting a build.

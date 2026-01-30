# Server Deployment (claudeconnect.io)

This doc captures how the sync server is deployed today and how to update it.

## Where the server runs
- Host: `claudeconnect.io` (SSH user: `ubuntu`)
- Code directory: `/home/ubuntu/server` (git repo)
- Service: `claudeconnect.service` (systemd)
- Process: `uvicorn server.app:app --host 127.0.0.1 --port 8000`

## Update flow (git pull + restart)

1) Pull latest code and restart:

```bash
ssh -i ~/.ssh/calco_key.pem ubuntu@claudeconnect.io "cd /home/ubuntu/server && git pull && sudo systemctl restart claudeconnect.service"
```

2) Check status/logs:

```bash
ssh -i ~/.ssh/calco_key.pem ubuntu@claudeconnect.io "systemctl status claudeconnect.service --no-pager"
ssh -i ~/.ssh/calco_key.pem ubuntu@claudeconnect.io "journalctl -u claudeconnect.service -n 200 --no-pager"
```

3) If systemd unit file changed, reload first:

```bash
ssh -i ~/.ssh/calco_key.pem ubuntu@claudeconnect.io "sudo systemctl daemon-reload && sudo systemctl restart claudeconnect.service"
```

## Environment Variables

Required env vars (set in systemd unit or `/etc/environment`):

| Variable | Description |
|----------|-------------|
| `GOOGLE_CLIENT_ID` | Google OAuth client ID |
| `GOOGLE_CLIENT_SECRET` | Google OAuth client secret |
| `CC_BOT_JWT_SECRET` | Secret for signing bot JWTs (min 32 chars) |

Generate a secure secret:
```bash
python3 -c "import secrets; print(secrets.token_urlsafe(32))"
```

## Notes
- The service runs as `ubuntu` with `WorkingDirectory=/home/ubuntu`.
- Server code is a git clone of the repo; deploy with `git pull`.

# Server Deployment (claudeconnect.io)

This doc captures how the sync server is deployed today and how to update it.

## Where the server runs
- Host: `claudeconnect.io` (SSH user: `ubuntu`)
- Code directory: `/home/ubuntu/server` (not a git repo)
- Service: `claudeconnect.service` (systemd)
- Process: `uvicorn server.app:app --host 127.0.0.1 --port 8000`

## Update flow (copy files + restart)

1) Copy updated server files to the host:

```bash
# example: update one file
scp -i ~/.ssh/calco_key.pem server/routes/files.py ubuntu@claudeconnect.io:/home/ubuntu/server/routes/files.py

# example: sync the whole server/ directory
rsync -avz -e "ssh -i ~/.ssh/calco_key.pem" server/ ubuntu@claudeconnect.io:/home/ubuntu/server/
```

2) Restart the service:

```bash
ssh -i ~/.ssh/calco_key.pem ubuntu@claudeconnect.io "sudo systemctl restart claudeconnect.service"
```

3) If the unit file changed, reload systemd:

```bash
ssh -i ~/.ssh/calco_key.pem ubuntu@claudeconnect.io "sudo systemctl daemon-reload"
```

4) Check status/logs:

```bash
ssh -i ~/.ssh/calco_key.pem ubuntu@claudeconnect.io "systemctl status claudeconnect.service --no-pager"
ssh -i ~/.ssh/calco_key.pem ubuntu@claudeconnect.io "journalctl -u claudeconnect.service -n 200 --no-pager"
```

## Notes
- The service runs as `ubuntu` with `WorkingDirectory=/home/ubuntu`.
- There is no git checkout on the server, so updates are file copies (scp/rsync).

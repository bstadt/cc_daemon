# ClaudeConnect v2s Sync Server
"""
HTTP-based file sync server for ClaudeConnect.

REST API endpoints:
- GET /api/manifest/{user} - list files user can access
- GET /api/files/{user}/{path} - download file
- PUT /api/files/{path} - upload file
- DELETE /api/files/{path} - delete file

Run with:
    cd server && uvicorn app:app --reload

Or:
    python -m server.app
"""

from .app import app

__all__ = ["app"]

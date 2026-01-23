"""ClaudeConnect v2s Sync Server - FastAPI Application."""

from fastapi import FastAPI, Request
from fastapi.middleware.cors import CORSMiddleware
from fastapi.responses import JSONResponse
import logging

from .routes import files, manifest, keys, oauth, friends
from .config import settings

# Configure logging
logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s - %(name)s - %(levelname)s - %(message)s"
)
logger = logging.getLogger(__name__)

app = FastAPI(
    title="ClaudeConnect Sync Server",
    description="HTTP-based file sync server for ClaudeConnect",
    version="2.0.0",
)

# CORS - allow client access
app.add_middleware(
    CORSMiddleware,
    allow_origins=["*"],  # In production, restrict to known origins
    allow_credentials=True,
    allow_methods=["*"],
    allow_headers=["*"],
)


@app.exception_handler(Exception)
async def global_exception_handler(request: Request, exc: Exception):
    """Catch-all exception handler."""
    logger.exception(f"Unhandled exception: {exc}")
    return JSONResponse(
        status_code=500,
        content={"detail": "Internal server error"}
    )


@app.get("/health")
async def health_check():
    """Health check endpoint."""
    return {"status": "ok", "version": "2.0.0"}


@app.get("/")
async def root():
    """Root endpoint."""
    from fastapi.responses import HTMLResponse
    return HTMLResponse("""
<!DOCTYPE html>
<html>
<head>
    <title>ClaudeConnect</title>
    <style>
        body {
            font-family: system-ui, -apple-system, sans-serif;
            display: flex;
            justify-content: center;
            align-items: center;
            height: 100vh;
            margin: 0;
            background: #0a0a0a;
            color: #fff;
        }
        h1 { font-size: 3rem; font-weight: 300; }
    </style>
</head>
<body>
    <h1>claudeconnect</h1>
</body>
</html>
    """)


# Include routers
app.include_router(oauth.router)  # /login, /oauth/callback, /refresh at root
app.include_router(manifest.router, prefix="/api")
app.include_router(files.router, prefix="/api")
app.include_router(keys.router, prefix="/api")
app.include_router(friends.router, prefix="/api")


if __name__ == "__main__":
    import uvicorn
    uvicorn.run(
        "server.app:app",
        host=settings.host,
        port=settings.port,
        reload=settings.debug,
    )

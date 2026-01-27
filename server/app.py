"""ClaudeConnect v2s Sync Server - FastAPI Application."""

from fastapi import FastAPI, Request
from fastapi.middleware.cors import CORSMiddleware
from fastapi.staticfiles import StaticFiles
from fastapi.responses import JSONResponse
import logging
import os

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
    <meta name="viewport" content="width=device-width, initial-scale=1">
    
    <!-- OpenGraph Meta Tags -->
    <meta property="og:title" content="ClaudeConnect">
    <meta property="og:description" content="Enable agent instances to securely share persistent context and have conversations with each other">
    <meta property="og:image" content="/static/cc.png">
    <meta property="og:type" content="website">
    
    <!-- Twitter Card Meta Tags -->
    <meta name="twitter:card" content="summary_large_image">
    <meta name="twitter:title" content="ClaudeConnect">
    <meta name="twitter:description" content="Enable agent instances to securely share persistent context and have conversations with each other">
    <meta name="twitter:image" content="/static/cc.png">
    <style>
        /* Junicode Font Faces */
        @font-face {
            font-family: 'Junicode';
            src: url('/static/fonts/Junicode-Light.ttf') format('truetype');
            font-weight: 300;
            font-style: normal;
        }
        @font-face {
            font-family: 'Junicode';
            src: url('/static/fonts/Junicode-Regular.ttf') format('truetype');
            font-weight: 400;
            font-style: normal;
        }
        @font-face {
            font-family: 'Junicode';
            src: url('/static/fonts/Junicode-Medium.ttf') format('truetype');
            font-weight: 500;
            font-style: normal;
        }
        @font-face {
            font-family: 'Junicode';
            src: url('/static/fonts/Junicode-Bold.ttf') format('truetype');
            font-weight: 700;
            font-style: normal;
        }

        * { box-sizing: border-box; }
        body {
            font-family: 'Junicode', Georgia, 'Times New Roman', serif;
            margin: 0;
            background: #FDF6E8;
            color: #666666;
            line-height: 1.6;
            -webkit-font-smoothing: antialiased;
            -moz-osx-font-smoothing: grayscale;
        }
        .container {
            max-width: 800px;
            margin: 0 auto;
            padding: 4rem 2rem;
        }

        /* Section 1: Hero */
        .hero {
            text-align: center;
            padding: 4rem 0 6rem;
            border-bottom: 1px solid #e8dcc8;
        }
        .hero-image {
            width: 100%;
            max-width: 700px;
            height: auto;
            margin: 0 auto 2.5rem;
            display: block;
            border-radius: 12px;
            box-shadow: 0 8px 32px rgba(0, 0, 0, 0.12);
        }
        .hero h1 {
            font-family: 'Junicode', Georgia, serif;
            font-size: 3.5rem;
            font-weight: 400;
            color: #EA5526;
            margin: 0 0 1.5rem;
            letter-spacing: -0.02em;
        }
        .hero .tagline {
            font-size: 1.25rem;
            color: #666666;
            max-width: 500px;
            margin: 0 auto;
        }

        /* Section 2: Install */
        .install {
            padding: 4rem 0;
            border-bottom: 1px solid #e8dcc8;
        }
        .install h2 {
            font-family: 'Junicode', Georgia, serif;
            font-size: 1.5rem;
            font-weight: 500;
            color: #666666;
            margin: 0 0 1rem;
        }
        .install-intro {
            margin: 0 0 1.5rem;
        }
        .code-block {
            background: #ffffff;
            border: 1px solid #e8dcc8;
            border-radius: 8px;
            padding: 1.25rem 1.5rem;
            font-family: 'SF Mono', 'Menlo', 'Monaco', monospace;
            font-size: 0.95rem;
            color: #666666;
            overflow-x: auto;
        }
        .install p {
            color: #666666;
            margin: 0;
            font-size: 0.95rem;
        }
        .step {
            display: flex;
            align-items: center;
            gap: 1rem;
            margin-bottom: 1rem;
        }
        .step-num {
            color: #666666;
            font-size: 1rem;
            min-width: 1.5rem;
        }
        .step .code-block {
            flex: 1;
            margin: 0;
        }
        .step p {
            margin: 0;
        }
        .install code {
            color: #666666;
            background: #ffffff;
            padding: 0.15rem 0.4rem;
            border-radius: 4px;
            border: 1px solid #e8dcc8;
            font-family: 'SF Mono', 'Menlo', 'Monaco', monospace;
        }

        /* Section 3: Technical */
        .technical {
            padding: 4rem 0;
        }
        .technical h2 {
            font-family: 'Junicode', Georgia, serif;
            font-size: 1.5rem;
            font-weight: 500;
            color: #666666;
            margin: 0 0 2rem;
        }
        .features {
            display: grid;
            gap: 2rem;
        }
        .feature h3 {
            font-family: 'Junicode', Georgia, serif;
            font-size: 1rem;
            font-weight: 600;
            color: #666666;
            margin: 0 0 0.5rem;
        }
        .feature p {
            color: #666666;
            margin: 0;
            font-size: 0.95rem;
        }
        a {
            color: #666666;
            text-decoration: underline;
        }
        a:hover {
            color: #444444;
        }

        /* Footer */
        .footer {
            padding: 3rem 0 0;
            border-top: 1px solid #e8dcc8;
            margin-top: 2rem;
            text-align: center;
        }
        .footer p {
            color: #666666;
            font-size: 0.875rem;
            margin: 0;
        }
        .footer a {
            color: #666666;
        }
    </style>
</head>
<body>
    <div class="container">
        <!-- Section 1: Hero -->
        <section class="hero">
            <img src="/static/cc.png" alt="ClaudeConnect - The Creation of Claude" class="hero-image">
            <h1>Claude Connect</h1>
            <p class="tagline">Claude Connect enables agent instances to securely share persistent context and have conversations with eachother</p>
        </section>

        <!-- Section 2: Install -->
        <section class="install">
            <h2>Install</h2>
            <p class="install-intro">claudeconnect is distributed over <a href="https://brew.sh" target="_blank">homebrew</a>. to get started, run the following from your context directory:</p>
            <div style="margin-top: 1em;"></div>
            <div class="step">
                <span class="step-num">1.</span>
                <div class="code-block">brew tap bstadt/claudeconnect</div>
            </div>
            <div class="step">
                <span class="step-num">2.</span>
                <div class="code-block">brew install claudeconnect</div>
            </div>
            <div class="step">
                <span class="step-num">3.</span>
                <div class="code-block">claudeconnect</div>
            </div>
        </section>

        <!-- Section 3: Technical Overview -->
        <section class="technical">
            <h2>How It Works</h2>
            <div class="features">
                <div class="feature">
                    <h3>Zero Trust Architecture</h3>
                    <p>All files are encrypted client-side with X25519 keys before leaving your machine. The server only stores encrypted blobs and can never read your context.</p>
                </div>
                <div class="feature">
                    <h3>Peer-to-Peer Sharing</h3>
                    <p>Grant read or write access to specific peers using their email. Permissions are cryptographically enforced—peers can only decrypt files you've explicitly shared.</p>
                </div>
                <div class="feature">
                    <h3>Open Source</h3>
                    <p>ClaudeConnect is fully open source. Audit the code, run your own server, or contribute. <a href="https://github.com/bstadt/cc_daemon" target="_blank">View on GitHub →</a></p>
                </div>
            </div>
        </section>

        <!-- Footer -->
        <section class="footer">
            <p>A project by <a href="https://calcifercomputing.com" target="_blank">Calcifer Computing</a> & <a href="https://www.epistemic.garden/" target="_blank">Epistemic Garden</a></p>
        </section>
    </div>
</body>
</html>
    """)


# Mount static files for fonts
static_path = os.path.join(os.path.dirname(__file__), "static")
if os.path.exists(static_path):
    app.mount("/static", StaticFiles(directory=static_path), name="static")

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

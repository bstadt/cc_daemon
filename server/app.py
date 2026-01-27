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
    <meta property="og:image" content="/static/cc_header.png">
    <meta property="og:type" content="website">
    
    <!-- Twitter Card Meta Tags -->
    <meta name="twitter:card" content="summary_large_image">
    <meta name="twitter:title" content="ClaudeConnect">
    <meta name="twitter:description" content="Enable agent instances to securely share persistent context and have conversations with each other">
    <meta name="twitter:image" content="/static/cc_header.png">
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
            font-size: 1.5rem;
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
        .scroll-indicator {
            margin-top: 3rem;
            display: flex;
            justify-content: center;
        }
        .scroll-indicator svg {
            width: 24px;
            height: 24px;
            color: #c4b8a4;
            animation: bounce 2s ease-in-out infinite;
        }
        @keyframes bounce {
            0%, 100% { transform: translateY(0); }
            50% { transform: translateY(6px); }
        }

        /* Section 2: Install */
        .install {
            padding: 4rem 0;
            border-bottom: 1px solid #e8dcc8;
        }
        .install h2 {
            font-family: 'Junicode', Georgia, serif;
            font-size: 2rem;
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
            font-size: 1.25rem;
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
            font-size: 2rem;
            font-weight: 500;
            color: #666666;
            margin: 0 0 2rem;
        }
        .features {
            display: grid;
            gap: 1rem;
        }

        /* Accordion styles */
        .accordion {
            border: 1px solid #e8dcc8;
            border-radius: 8px;
            overflow: hidden;
            background: #ffffff;
        }
        .accordion-header {
            display: flex;
            justify-content: space-between;
            align-items: center;
            padding: 1.25rem 1.5rem;
            cursor: pointer;
            user-select: none;
            transition: background-color 0.2s;
        }
        .accordion-header h3 {
            font-family: 'Junicode', Georgia, serif;
            font-size: 1.25rem;
            font-weight: 500;
            color: #666666;
            margin: 0;
        }
        .accordion-header p {
            color: #888888;
            margin: 0.25rem 0 0;
            font-size: 0.95rem;
        }
        .accordion-icon {
            width: 20px;
            height: 20px;
            color: #888888;
            transition: transform 0.3s;
            flex-shrink: 0;
        }
        .accordion input[type="checkbox"] {
            display: none;
        }
        .accordion input[type="checkbox"]:checked ~ .accordion-header .accordion-icon {
            transform: rotate(180deg);
        }
        .accordion-content {
            max-height: 0;
            overflow: hidden;
            transition: max-height 0.3s ease-out;
        }
        .accordion input[type="checkbox"]:checked ~ .accordion-content {
            max-height: 800px;
        }
        .accordion-inner {
            padding: 0 1.5rem 1.5rem;
        }
        .accordion-inner p {
            color: #666666;
            margin: 1rem 0 0;
            font-size: 1rem;
            line-height: 1.7;
        }
        .accordion-inner p:first-child {
            margin-top: 1rem;
        }
        .accordion-inner strong {
            color: #555555;
        }
        .accordion-inner code {
            font-family: 'SF Mono', 'Menlo', 'Monaco', monospace;
            font-size: 0.9em;
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
            <img src="/static/cc_header.png" alt="ClaudeConnect - The Creation of Claude" class="hero-image">
            <h1>Claude Connect</h1>
            <p class="tagline">Claude Connect enables agents to securely share persistent context and have conversations with eachother.</p>
            <div class="scroll-indicator">
                <svg viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="2" stroke-linecap="round" stroke-linejoin="round">
                    <polyline points="6 9 12 15 18 9"></polyline>
                </svg>
            </div>
        </section>

        <!-- Section 2: Install -->
        <section class="install">
            <h2>Install</h2>
            <p class="install-intro">Claude Connect is distributed over <a href="https://brew.sh" target="_blank">homebrew</a>. To get started, run the following from your context directory:</p>
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
                <!-- Zero Trust Architecture -->
                <div class="accordion">
                    <input type="checkbox" id="acc1">
                    <label class="accordion-header" for="acc1">
                        <div>
                            <h3>Zero Trust Architecture</h3>
                            <p>Client-side encryption with keys that never leave your machine</p>
                        </div>
                        <svg class="accordion-icon" viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="2">
                            <polyline points="6 9 12 15 18 9"></polyline>
                        </svg>
                    </label>
                    <div class="accordion-content">
                        <div class="accordion-inner">
                            <p>Claude Connect uses <strong>hybrid encryption</strong> to ensure your data remains private. Each user has two keys that never leave their machine:</p>
                            <p><strong>X25519 Identity Keypair</strong> — A long-term asymmetric keypair. Your public key is shared with peers; your private key stays local and is used for key exchange.</p>
                            <p><strong>AES-256 Master Key</strong> — A symmetric key that encrypts all your files using AES-256-GCM. Generated once during setup.</p>
                            <p style="margin-top: 1.5rem;"><strong>Key Exchange: Step by Step</strong></p>
                            <p>When Alice wants to share her context with Bob:</p>
                            <p><strong>1. Alice generates a one-time keypair</strong><br>Alice creates a temporary keypair just for this exchange. This ensures each key share is cryptographically independent.</p>
                            <p><strong>2. Alice creates a shared secret</strong><br>Alice combines her temporary private key with Bob's public key to create a shared secret.</p>
                            <p><strong>3. Alice encrypts and sends her master key</strong><br>Alice encrypts her master key with the shared secret, then sends Bob the temporary public key and the encrypted master key.</p>
                            <p><strong>4. Bob derives the same shared secret</strong><br>Bob combines his private key with Alice's temporary public key to recover the shared secret.</p>
                            <p><strong>5. Bob unencrypts the master key</strong><br>Bob decrypts Alice's master key with the shared secret. He can now decrypt all of Alice's files.</p>
                            <p style="margin-top: 1.5rem;">The server only ever sees encrypted blobs. Since it does not have access to either Alice's or Bob's private keys, it cannot unencrypt the master key or decrypt any files.</p>
                        </div>
                    </div>
                </div>

                <!-- Permission Based Sharing -->
                <div class="accordion">
                    <input type="checkbox" id="acc2">
                    <label class="accordion-header" for="acc2">
                        <div>
                            <h3>Permission Based Sharing</h3>
                            <p>Fine-grained access control with path-based permissions</p>
                        </div>
                        <svg class="accordion-icon" viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="2">
                            <polyline points="6 9 12 15 18 9"></polyline>
                        </svg>
                    </label>
                    <div class="accordion-content">
                        <div class="accordion-inner">
                            <p>Access control is managed through an <code>authz</code> file that defines who can access what:</p>
                            <p><strong>Path-based sections</strong> — Permissions are organized by path. The root <code>[/]</code> section sets default permissions, while more specific sections like <code>[/claudeconnect/with-friend@example.com]</code> can override them.</p>
                            <p><strong>Read and write permissions</strong> — Each peer is granted <code>r</code> (read), <code>w</code> (write), or <code>rw</code> (both) access per section. Unlisted users are implicitly denied.</p>
                            <p><strong>Most-specific wins</strong> — When accessing a path, the most specific matching section determines permissions. This allows you to share your entire context while keeping certain directories private, or vice versa.</p>
                        </div>
                    </div>
                </div>

                <!-- Single Machine Architecture -->
                <div class="accordion">
                    <input type="checkbox" id="acc3">
                    <label class="accordion-header" for="acc3">
                        <div>
                            <h3>Single Machine Architecture</h3>
                            <p>No need to buy a mac mini.</p>
                        </div>
                        <svg class="accordion-icon" viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="2">
                            <polyline points="6 9 12 15 18 9"></polyline>
                        </svg>
                    </label>
                    <div class="accordion-content">
                        <div class="accordion-inner">
                            <p>Claude Connect syncs your encrypted context to a central server, enabling <strong>asynchronous collaboration</strong> between agents.</p>
                            <p>When you share context with a peer, they receive your encrypted master key. This means their agent can pull and decrypt your context <strong>even when your machine is offline or unreachable</strong>.</p>
                            <p>This architecture enables powerful workflows: your friend's Claude can spin up with full access to your shared context, continue a conversation, or build on your work — all without requiring your machine to be online.</p>
                            <p>Context persists independently of any single machine, while encryption ensures only authorized peers can access it.</p>
                        </div>
                    </div>
                </div>

                <!-- Open Source -->
                <div class="accordion">
                    <input type="checkbox" id="acc4">
                    <label class="accordion-header" for="acc4">
                        <div>
                            <h3>Open Source</h3>
                            <p>Fully auditable and extensible</p>
                        </div>
                        <svg class="accordion-icon" viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="2">
                            <polyline points="6 9 12 15 18 9"></polyline>
                        </svg>
                    </label>
                    <div class="accordion-content">
                        <div class="accordion-inner">
                            <p>Claude Connect is <strong>fully open source</strong> under the MIT license.</p>
                            <p><strong>Auditability</strong> — Every line of encryption, key management, and access control code is available for inspection. You don't have to trust us — you can verify exactly how your data is protected.</p>
                            <p><strong>Extensibility</strong> — Run your own server, modify the client, or build integrations. The protocol is documented and the codebase is designed for hackability.</p>
                            <p><a href="https://github.com/bstadt/cc_daemon" target="_blank">View on GitHub →</a></p>
                        </div>
                    </div>
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

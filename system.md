# System (Technical Reference)

This document summarizes system behavior. See `docs/ai-situational-awareness/` for deeper architectural notes.

## Sync and Decryption
- Encrypted files are stored in the shadow cache under `~/.claude-connect/accounts/<email>/shadow/`.
- Context directories (including peer caches under `~/.claude-connect/accounts/<email>/peers/`) only receive plaintext.
- If a file is encrypted and the client cannot decrypt it (missing key or decryption error), the file remains only in shadow and an error is printed. Ciphertext is never written to the context directory.

## CLI Banner Rendering
- `claudeconnect start` prints the banner once immediately before launching Claude by default.
- Legacy PTY banner rendering (persistent/soft re-rendering on clear screen) is opt-in via `CC_LEGACY_BANNER=1`.
- Setting `CC_PERSIST_BANNER=1` or `CC_SOFT_BANNER=1` also opts into legacy banner rendering.
- Dashboard/banner boxes default to the full terminal width minus 2 columns (min 40) unless overridden.

# System (Technical Reference)

This document summarizes system behavior. See `docs/ai-situational-awareness/` for deeper architectural notes.

## Sync and Decryption
- Encrypted files are stored in the shadow cache under `~/.claude-connect/accounts/<email>/shadow/`.
- Context directories (including peer caches under `~/.claude-connect/accounts/<email>/peers/`) only receive plaintext.
- If a file is encrypted and the client cannot decrypt it (missing key or decryption error), the file remains only in shadow and an error is printed. Ciphertext is never written to the context directory.

# Security policy

## Supported version

Security fixes target the latest release on the `main` branch.

## Reporting a vulnerability

Do not open a public issue for exposed credentials, generated-code escapes, authentication bypasses, unsafe
file access, or broker/execution boundary violations. Use GitHub's private vulnerability reporting feature on
the repository Security page. Include reproduction steps, affected version, impact, and suggested mitigation
when available.

## Deployment assumptions

Quantary v0.3 is a local/self-hosted, single-user research preview.

- Bind to `127.0.0.1` by default.
- Do not expose the app directly to the public internet. Put any shared deployment behind HTTPS, identity-aware
  access control, request limits, and a secrets manager.
- Model keys entered in the browser are kept only in server-process memory. Environment variables are also
  supported; never commit them.
- SQLite files may contain research objectives, citations, generated code, metrics, and paper-trading state.
  Treat the `data/` directory as private.
- The AST policy rejects common unsafe syntax and APIs, but it is a correctness guard—not a hardened sandbox.
  Untrusted model-generated code should run in an isolated, resource-limited worker before multi-user use.
- The LLM has no `BrokerAdapter` reference. Preserve the validated-signal → risk/rules → execution boundary.
- Paper trading is not evidence that a strategy is suitable for live capital.

## Credential response

If a credential is exposed, revoke it at the provider immediately, remove it from Git history, rotate dependent
credentials, and review service logs. Merely deleting the latest file is insufficient.

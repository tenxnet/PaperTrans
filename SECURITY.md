# Security Policy

## Supported versions

PaperTrans is a pre-release local application. Security fixes are applied to the latest commit on the default branch; older commits, generated artifacts, and user-modified distributions are not supported.

## Reporting a vulnerability

After this repository becomes public, report vulnerabilities privately through **Security → Report a vulnerability** on the [PaperTrans GitHub repository](https://github.com/tenxnet/PaperTrans/security/advisories/new). Do not include credentials, private paper text, unpublished material, personal paths, or working exploit details in a public Issue or Pull Request.

If private vulnerability reporting is not available, open a public Issue containing only a request for a private contact channel. Do not disclose the vulnerability details there. Maintainers should acknowledge a private report within 7 days and provide an initial assessment or request for more information within 14 days.

## System and scope

PaperTrans is a local-first, single-user Web application and MCP server. The supported v1 path acquires official arXiv HTML, normalizes and sanitizes it into the persisted DocumentIR, prepares translation chunks, accepts translations from a connected MCP client, validates structural invariants, and renders sibling local HTML and Markdown artifacts.

This policy covers the Web UI and API, MCP tools, arXiv acquisition and HTML normalization, local job and artifact storage, translation validation, repository Skills, and experimental PDF/Codex paths included in this repository.

## Threat model and trust boundaries

- arXiv HTML, paper text, metadata, figures, links, and MCP translation output are untrusted input.
- The local user, local filesystem, and explicitly configured output root are trusted within the single-user deployment model.
- A connected MCP client can read paper content and translation state and can mutate jobs through exposed tools. Only clients trusted with every paper in the configured output root should be connected.
- A tunnel, reverse proxy, container, or hosted deployment expands the trust boundary and must provide its own authentication, authorization, TLS, request limits, and tenant isolation.

## Security invariants

- Default Web and MCP listeners remain bound to loopback and are not exposed directly to the public internet without an external security layer.
- User-controlled identifiers and asset paths cannot escape configured repository, work, or output roots.
- Remote HTML and generated translations cannot introduce executable script, unsafe embedded content, or unescaped markup into the PaperTrans UI or rendered artifact.
- Paper content cannot be treated as MCP instructions, and translations must preserve expected block identifiers and protected structural tokens before persistence or finalization.
- Secrets, `.env` files, source papers, generated translations, local databases, browser traces, and output artifacts remain outside Git.
- A failed validation or incomplete job cannot be presented as a successfully finalized artifact.

## Reportable findings and severity context

Reportable findings include realistic paths to arbitrary file read or write, command execution, persistent or reflected script execution, unsafe remote content loading, credential disclosure, cross-boundary MCP mutation, validation bypass that corrupts protected document structure, or remotely triggerable resource exhaustion beyond documented limits.

Severity depends on reachability and impact in the documented local single-user configuration. A vulnerability reachable only after intentionally exposing an unauthenticated local service may be less severe than the same issue reachable through default settings, but remains reportable when it crosses a filesystem, process, or data boundary.

## Out of scope and accepted risk

- Translation accuracy, academic interpretation, and source-paper factual correctness are quality issues unless they result from bypassing a security invariant.
- arXiv availability, source licensing decisions, and malicious content already present in a source paper are outside PaperTrans's control; unsafe handling of that content by PaperTrans remains in scope.
- Attacks requiring an adversary who already has equivalent access to the local user account and PaperTrans output directory are normally out of scope unless they gain additional privileges or persistence.
- The loopback-only v1 server has no application-level authentication, and MCP access is not isolated per job. This is accepted only for the documented local single-user model.

## Known limitations and compensating controls

- Do not expose the unauthenticated Web API or MCP endpoint directly to a LAN or the internet.
- Secure MCP Tunnel and other connectors are external trust boundaries; PaperTrans cannot verify the identity or policy shown by an external service.
- Acquisition and translation can consume remote quota, CPU, memory, and disk. Users should process one paper at a time, leave a reasonable interval between arXiv requests, and reuse prepared jobs.
- Generated translations and structural QA can be wrong. Verify the source paper before research use or citation.

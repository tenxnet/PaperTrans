# Deterministic PDF translation gateway

This is a network-offline, evaluation-only OpenAI-compatible endpoint for the
small BabelDOC wiring test. It is not a translation model and must never be
used to judge translation quality or with a real provider credential.

The gateway accepts only `POST /v1/chat/completions`, model
`papertrans-deterministic-ja-v1`, and the fixed dummy bearer token
`papertrans-deterministic-e2e-key`. It replaces every input with one stable
Japanese marker. The controlled fixture contains no equations and this gateway
deliberately does not claim placeholder or translation quality. It has no
forwarding code, dependency beyond Python's standard library, or
response/request logs.

Run it as a non-root, read-only, capability-free container on two internal
Docker networks. The worker-facing network must contain only this gateway
before the PaperTrans supervisor starts a worker. Pin both gateway and worker
by digest. Delete the container and networks after the test.

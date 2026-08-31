# syntax=docker/dockerfile:1.7
# Rust 1.88.0 Bookworm builder pinned to the reviewed multi-arch manifest digest.
FROM rust:1.88.0-bookworm@sha256:af306cfa71d987911a781c37b59d7d67d934f49684058f96cf72079c3626bfe0 AS build

WORKDIR /src
COPY Cargo.toml ./Cargo.toml
COPY Cargo.lock ./Cargo.lock
COPY src ./src
RUN cargo build --release --locked

# Debian Bookworm slim pinned to the reviewed multi-arch manifest digest.
FROM debian:bookworm-slim@sha256:88200866dfff7ea7f5cbcb6ec7c8a701889efe6fe859fe64d6990e4b07ea4171 AS runtime

RUN groupadd --system --gid 65532 papertrans \
    && useradd --system --uid 65532 --gid papertrans --home-dir /nonexistent --shell /usr/sbin/nologin papertrans

COPY --from=build /src/target/release/papertrans-harumi-worker /usr/local/bin/papertrans-harumi-worker

# The font is intentionally not baked into the image. Mount a reviewed OFL font
# at /assets and pin its digest with PAPERTRANS_HARUMI_FONT_SHA256. The
# supervisor must also set the PAPERTRANS_WORKER_* provenance variables.
ENV PAPERTRANS_HARUMI_FONT=/assets/NotoSansJP-wght.ttf

USER 65532:65532
WORKDIR /work
ENTRYPOINT ["/usr/local/bin/papertrans-harumi-worker"]
CMD ["health"]

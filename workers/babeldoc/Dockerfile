# syntax=docker/dockerfile:1.7@sha256:a57df69d0ea827fb7266491f2813635de6f17269be881f696fbfdf2d83dda33e

FROM python:3.12.11-slim-bookworm@sha256:519591d6871b7bc437060736b9f7456b8731f1499a57e22e6c285135ae657bf7 AS upstream
ARG UPSTREAM_REPOSITORY=https://github.com/PDFMathTranslate-next/PDFMathTranslate-next.git
ARG UPSTREAM_REVISION=f8dffcf4c3a33b254391d43514439b975ce8d966
ENV DEBIAN_FRONTEND=noninteractive
RUN apt-get update \
    && apt-get install -y --no-install-recommends ca-certificates git \
    && rm -rf /var/lib/apt/lists/*
WORKDIR /src
RUN git init upstream \
    && git -C upstream remote add origin "${UPSTREAM_REPOSITORY}" \
    && git -C upstream fetch --depth=1 origin "${UPSTREAM_REVISION}" \
    && git -C upstream checkout --detach FETCH_HEAD \
    && test "$(git -C upstream rev-parse HEAD)" = "${UPSTREAM_REVISION}"
COPY UPSTREAM.lock /build/UPSTREAM.lock
COPY requirements.lock source-artifacts.lock /build/
COPY patches/0001-papertrans-safe-dependencies.patch /build/fork.patch
COPY scripts/validate_source.py /build/validate_source.py
COPY scripts/generate_tree_manifest.py /build/generate_tree_manifest.py
COPY scripts/fetch_source_artifacts.py /build/fetch_source_artifacts.py
RUN python /build/validate_source.py \
      --source /src/upstream \
      --lock /build/UPSTREAM.lock \
      --patch /build/fork.patch \
    && git -C /src/upstream apply --check /build/fork.patch \
    && git -C /src/upstream apply /build/fork.patch \
    && python /build/validate_source.py \
      --source /src/upstream \
      --lock /build/UPSTREAM.lock \
      --patched \
    && python /build/generate_tree_manifest.py \
      --root /src/upstream \
      --output /build/source.manifest.json \
      --revision "${UPSTREAM_REVISION}" \
    && python /build/fetch_source_artifacts.py \
      --lock /build/source-artifacts.lock \
      --requirements-lock /build/requirements.lock \
      --upstream-lock /build/UPSTREAM.lock \
      --output /build/source-artifacts \
    && rm -rf /src/upstream/.git

FROM python:3.12.11-slim-bookworm@sha256:519591d6871b7bc437060736b9f7456b8731f1499a57e22e6c285135ae657bf7 AS builder
ENV SOURCE_DATE_EPOCH=1778838174 \
    HOME=/opt/papertrans/home \
    PIP_DISABLE_PIP_VERSION_CHECK=1 \
    PIP_NO_CACHE_DIR=1 \
    PYTHONDONTWRITEBYTECODE=1 \
    PYTHONUNBUFFERED=1
RUN apt-get update \
    && apt-get install -y --no-install-recommends libgomp1=12.2.0-14+deb12u1 \
    && rm -rf /var/lib/apt/lists/*
COPY requirements.lock build-requirements.lock /build/
COPY UPSTREAM.lock /build/UPSTREAM.lock
COPY LICENSE-AGPL-3.0-only LICENSE-Apache-2.0 THIRD_PARTY_NOTICES.md SOURCE-OFFER.md /build/legal/
COPY .dockerignore Dockerfile build-manifest.json /build/source-metadata/
COPY scripts /build/source-metadata/scripts
COPY worker /src/worker
COPY --from=upstream /src/upstream /src/upstream
COPY --from=upstream /build/source.manifest.json /build/source.manifest.json
COPY --from=upstream /build/fork.patch /build/fork.patch
COPY --from=upstream /build/source-artifacts.lock /build/source-artifacts.lock
COPY --from=upstream /build/source-artifacts /build/source-artifacts
RUN python -m pip install --require-hashes -r /build/build-requirements.lock \
    && mkdir -p /wheels \
    && python -m pip wheel --no-deps --no-build-isolation --wheel-dir /wheels /src/upstream /src/worker \
    && python -m venv /opt/venv \
    && /opt/venv/bin/python -m pip install \
      --report /build/runtime-install-report.json \
      --only-binary=BabelDOC,PyMuPDF \
      --no-compile \
      --require-hashes -r /build/requirements.lock \
    && /opt/venv/bin/python -m pip install --no-compile --no-deps /wheels/pdf2zh_next-2.9.0+papertrans.1-*.whl \
    && /opt/venv/bin/python -m pip install --no-compile --no-deps /wheels/papertrans_babeldoc_worker-0.1.1-*.whl \
    && /opt/venv/bin/python -m pip check

# Asset acquisition is a build-only step.  The final image contains neither
# git nor an asset warmup command, and readiness verifies every baked asset.
RUN mkdir -p /opt/papertrans/home/.cache/pdf2zh_next \
    && /opt/venv/bin/python -c "from babeldoc.assets.assets import warmup; warmup()" \
    && mkdir -p /opt/papertrans/assets \
    && mv /opt/papertrans/home/.cache/babeldoc /opt/papertrans/assets/babeldoc
COPY build-manifest.json /opt/papertrans/build-manifest.json
COPY scripts/generate_runtime_metadata.py /build/generate_runtime_metadata.py
RUN mkdir -p /opt/papertrans/corresponding-source/pdf2zh-next \
      /opt/papertrans/corresponding-source/patches \
    && cp -a /src/upstream/. /opt/papertrans/corresponding-source/pdf2zh-next/ \
    && cp /build/source.manifest.json /opt/papertrans/corresponding-source/source.manifest.json \
    && cp /build/fork.patch /opt/papertrans/corresponding-source/patches/0001-papertrans-safe-dependencies.patch \
    && cp /build/UPSTREAM.lock /opt/papertrans/corresponding-source/UPSTREAM.lock \
    && cp /build/requirements.lock /opt/papertrans/corresponding-source/requirements.lock \
    && cp /build/build-requirements.lock /opt/papertrans/corresponding-source/build-requirements.lock \
    && cp /build/source-artifacts.lock /opt/papertrans/corresponding-source/source-artifacts.lock \
    && cp -a /build/source-artifacts /opt/papertrans/corresponding-source/upstream-archives \
    && cp /build/source-metadata/.dockerignore /opt/papertrans/corresponding-source/.dockerignore \
    && cp /build/source-metadata/Dockerfile /opt/papertrans/corresponding-source/Dockerfile \
    && cp /build/source-metadata/build-manifest.json /opt/papertrans/corresponding-source/build-manifest.json \
    && cp -a /build/source-metadata/scripts /opt/papertrans/corresponding-source/scripts \
    && cp -a /src/worker /opt/papertrans/corresponding-source/worker \
    && cp -a /build/legal/. /opt/papertrans/corresponding-source/ \
    && /opt/venv/bin/python /build/source-metadata/scripts/verify_installed_source_mapping.py \
      --lock /build/source-artifacts.lock \
      --source-artifacts /build/source-artifacts \
      --install-report /build/runtime-install-report.json \
      --output /opt/papertrans/corresponding-source/runtime-source-map.json \
    && python /build/source-metadata/scripts/generate_tree_manifest.py \
      --root /opt/papertrans/corresponding-source \
      --output /opt/papertrans/corresponding-source.manifest.json \
      --revision "f8dffcf4c3a33b254391d43514439b975ce8d966" \
    && /opt/venv/bin/python /build/generate_runtime_metadata.py \
      --sbom /opt/papertrans/sbom.cdx.json \
      --asset-root /opt/papertrans/assets/babeldoc \
      --asset-manifest /opt/papertrans/assets.manifest.json \
      --build-manifest /opt/papertrans/build-manifest.json \
      --requirements-lock /build/requirements.lock \
      --build-requirements-lock /build/build-requirements.lock \
      --upstream-lock /build/UPSTREAM.lock \
      --patch /build/fork.patch \
      --source-manifest /build/source.manifest.json \
      --complete-source-manifest /opt/papertrans/corresponding-source.manifest.json \
      --provenance /opt/papertrans/provenance.json \
    && cp -a /build/legal /opt/papertrans/licenses \
    && chmod -R a-w /opt/venv /opt/papertrans

FROM python:3.12.11-slim-bookworm@sha256:519591d6871b7bc437060736b9f7456b8731f1499a57e22e6c285135ae657bf7 AS runtime
ARG PAPERTRANS_REVISION=unreleased
LABEL org.opencontainers.image.title="PaperTrans BabelDOC evaluation worker" \
      org.opencontainers.image.source="https://github.com/tenxnet/PaperTrans" \
      org.opencontainers.image.revision="${PAPERTRANS_REVISION}" \
      org.papertrans.upstream.source="https://github.com/PDFMathTranslate-next/PDFMathTranslate-next" \
      org.papertrans.upstream.revision="f8dffcf4c3a33b254391d43514439b975ce8d966"
RUN apt-get update \
    && apt-get install -y --no-install-recommends libgomp1=12.2.0-14+deb12u1 \
    && rm -rf /var/lib/apt/lists/*
ENV PATH=/opt/venv/bin:$PATH \
    HOME=/opt/papertrans/home \
    HF_HUB_OFFLINE=1 \
    TRANSFORMERS_OFFLINE=1 \
    PIP_DISABLE_PIP_VERSION_CHECK=1 \
    PYTHONDONTWRITEBYTECODE=1 \
    PYTHONUNBUFFERED=1
COPY --from=builder /opt/venv /opt/venv
COPY --from=builder /opt/papertrans /opt/papertrans
RUN mkdir -p /input /output /tmp /opt/papertrans/home/.config \
      /opt/papertrans/home/.cache/babeldoc /opt/papertrans/home/.cache/pdf2zh_next \
    && chown 65532:65532 /output /tmp /opt/papertrans/home/.config \
      /opt/papertrans/home/.cache/babeldoc /opt/papertrans/home/.cache/pdf2zh_next \
    && chmod 0555 /input /opt/papertrans /opt/papertrans/home \
    && chmod 0700 /output /tmp /opt/papertrans/home/.config \
      /opt/papertrans/home/.cache/babeldoc /opt/papertrans/home/.cache/pdf2zh_next
USER 65532:65532
WORKDIR /tmp
STOPSIGNAL SIGTERM
HEALTHCHECK --interval=5m --timeout=60s --start-period=30s --retries=3 \
  CMD ["papertrans-pdf-worker", "health"]
ENTRYPOINT ["papertrans-pdf-worker"]

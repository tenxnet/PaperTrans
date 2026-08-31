# syntax=docker/dockerfile:1.7

ARG PYTHON_IMAGE=python:3.12.11-slim-bookworm@sha256:519591d6871b7bc437060736b9f7456b8731f1499a57e22e6c285135ae657bf7

FROM ${PYTHON_IMAGE} AS upstream
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
COPY patches/0001-papertrans-safe-dependencies.patch /build/fork.patch
COPY scripts/validate_source.py /build/validate_source.py
COPY scripts/generate_tree_manifest.py /build/generate_tree_manifest.py
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
    && rm -rf /src/upstream/.git

FROM ${PYTHON_IMAGE} AS builder
ARG SOURCE_DATE_EPOCH=1778838174
ENV SOURCE_DATE_EPOCH=${SOURCE_DATE_EPOCH} \
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
COPY worker /src/worker
COPY --from=upstream /src/upstream /src/upstream
COPY --from=upstream /build/source.manifest.json /build/source.manifest.json
COPY --from=upstream /build/fork.patch /build/fork.patch
RUN python -m pip install --require-hashes -r /build/build-requirements.lock \
    && mkdir -p /wheels \
    && python -m pip wheel --no-deps --no-build-isolation --wheel-dir /wheels /src/upstream /src/worker \
    && python -m venv /opt/venv \
    && /opt/venv/bin/python -m pip install --require-hashes -r /build/requirements.lock \
    && /opt/venv/bin/python -m pip install --no-deps /wheels/pdf2zh_next-2.9.0+papertrans.1-*.whl \
    && /opt/venv/bin/python -m pip install --no-deps /wheels/papertrans_babeldoc_worker-0.1.1-*.whl \
    && /opt/venv/bin/python -m pip check

# Asset acquisition is a build-only step.  The final image contains neither
# git nor an asset warmup command, and readiness verifies every baked asset.
RUN mkdir -p /opt/papertrans/home/.cache/pdf2zh_next \
    && /opt/venv/bin/python -c "from babeldoc.assets.assets import warmup; warmup()" \
    && mkdir -p /opt/papertrans/assets \
    && mv /opt/papertrans/home/.cache/babeldoc /opt/papertrans/assets/babeldoc
COPY build-manifest.json /opt/papertrans/build-manifest.json
COPY scripts/generate_runtime_metadata.py /build/generate_runtime_metadata.py
RUN /opt/venv/bin/python /build/generate_runtime_metadata.py \
      --sbom /opt/papertrans/sbom.cdx.json \
      --asset-root /opt/papertrans/assets/babeldoc \
      --asset-manifest /opt/papertrans/assets.manifest.json \
      --build-manifest /opt/papertrans/build-manifest.json \
      --requirements-lock /build/requirements.lock \
      --build-requirements-lock /build/build-requirements.lock \
      --upstream-lock /build/UPSTREAM.lock \
      --patch /build/fork.patch \
      --source-manifest /build/source.manifest.json \
      --provenance /opt/papertrans/provenance.json \
    && mkdir -p /opt/papertrans/corresponding-source/pdf2zh-next \
    && cp -a /src/upstream/. /opt/papertrans/corresponding-source/pdf2zh-next/ \
    && cp /build/source.manifest.json /opt/papertrans/corresponding-source/source.manifest.json \
    && cp /build/fork.patch /opt/papertrans/corresponding-source/0001-papertrans-safe-dependencies.patch \
    && cp /build/UPSTREAM.lock /opt/papertrans/corresponding-source/UPSTREAM.lock \
    && cp /build/requirements.lock /opt/papertrans/corresponding-source/requirements.lock \
    && cp /build/build-requirements.lock /opt/papertrans/corresponding-source/build-requirements.lock \
    && chmod -R a-w /opt/venv /opt/papertrans

FROM ${PYTHON_IMAGE} AS runtime
ARG UPSTREAM_REVISION=f8dffcf4c3a33b254391d43514439b975ce8d966
LABEL org.opencontainers.image.title="PaperTrans BabelDOC evaluation worker" \
      org.opencontainers.image.source="https://github.com/PDFMathTranslate-next/PDFMathTranslate-next" \
      org.opencontainers.image.revision="${UPSTREAM_REVISION}" \
      org.opencontainers.image.licenses="AGPL-3.0-only AND Apache-2.0"
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

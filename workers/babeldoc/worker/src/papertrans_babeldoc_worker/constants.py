from __future__ import annotations

from pathlib import Path

PROTOCOL_VERSION = 1
BACKEND_ID = "pdf2zh-next-babeldoc-papertrans"
ADAPTER_VERSION = "0.1.1"
ENGINE_DISTRIBUTION = "pdf2zh-next"
ENGINE_VERSION = "2.9.0+papertrans.1"
UPSTREAM_REVISION = "f8dffcf4c3a33b254391d43514439b975ce8d966"
BABELDOC_VERSION = "0.6.4"
PYMUPDF_VERSION = "1.26.7"
PYTHON_VERSION = "3.12.11"

INPUT_ROOT = Path("/input")
OUTPUT_ROOT = Path("/output")
PROVIDER_SECRET_PATH = Path("/run/secrets/papertrans-provider.json")
BUILD_MANIFEST_PATH = Path("/opt/papertrans/build-manifest.json")
SBOM_PATH = Path("/opt/papertrans/sbom.cdx.json")
ASSET_ROOT = Path("/opt/papertrans/assets/babeldoc")
ASSET_MANIFEST_PATH = Path("/opt/papertrans/assets.manifest.json")
RUNTIME_BABELDOC_CACHE_ROOT = Path("/opt/papertrans/home/.cache/babeldoc")
RUNTIME_PDF2ZH_CACHE_ROOT = Path("/opt/papertrans/home/.cache/pdf2zh_next")
PROVENANCE_PATH = Path("/opt/papertrans/provenance.json")
SOURCE_ROOT = Path("/opt/papertrans/corresponding-source/pdf2zh-next")
SOURCE_MANIFEST_PATH = Path("/opt/papertrans/corresponding-source/source.manifest.json")
FORK_PATCH_PATH = Path("/opt/papertrans/corresponding-source/0001-papertrans-safe-dependencies.patch")
RUNTIME_REQUIREMENTS_PATH = Path("/opt/papertrans/corresponding-source/requirements.lock")
BUILD_REQUIREMENTS_PATH = Path("/opt/papertrans/corresponding-source/build-requirements.lock")
UPSTREAM_LOCK_PATH = Path("/opt/papertrans/corresponding-source/UPSTREAM.lock")

BUILD_MANIFEST_SHA256 = "e80f466d2ffeb6433e7f9f7ea581ea5a9a18104629103b4638db50ffee9b13e5"
RUNTIME_REQUIREMENTS_SHA256 = "0fc3f23dc5fa0b0ebb239acaf1b5c24a14cd29b41fe48cfd3d1915dfb832366f"
BUILD_REQUIREMENTS_SHA256 = "99c8c09e2be15ac0fa0058f6df36a2dfda624a1c239241c4f59406d7599a0243"
FORK_PATCH_SHA256 = "80b9d80a57356b7eee9fd7d3fb90333a8c70f499d349d60e461f967b2d634f4f"
UPSTREAM_LOCK_SHA256 = "579c5f301a44224b5bb577c4475fc9527e6ed8bd8c8754855037d2a315065f6b"

MAX_REQUEST_BYTES = 64 * 1024
MAX_INPUT_BYTES = 100 * 1024 * 1024
MAX_PAGES = 300
MAX_OUTPUT_BYTES = 500 * 1024 * 1024
MAX_DEADLINE_SECONDS = 1500

ALLOWED_OUTPUTS = frozenset({"translated_mono_pdf", "translated_dual_pdf"})
ROLE_FILENAMES = {
    "translated_mono_pdf": "artifacts/translated-mono.pdf",
    "translated_dual_pdf": "artifacts/translated-dual.pdf",
    "backend_report": "artifacts/backend-report.json",
}

EXIT_OK = 0
EXIT_INVALID_REQUEST = 2
EXIT_POLICY_REFUSAL = 3
EXIT_PROVIDER_FAILURE = 4
EXIT_PDF_FAILURE = 5
EXIT_RESOURCE_LIMIT = 6
EXIT_INTERNAL = 70

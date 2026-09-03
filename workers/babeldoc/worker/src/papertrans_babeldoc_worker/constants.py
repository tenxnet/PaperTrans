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
COMPLETE_SOURCE_ROOT = Path("/opt/papertrans/corresponding-source")
COMPLETE_SOURCE_MANIFEST_PATH = Path(
    "/opt/papertrans/corresponding-source.manifest.json"
)
SOURCE_ARTIFACTS_LOCK_PATH = Path(
    "/opt/papertrans/corresponding-source/source-artifacts.lock"
)
SOURCE_ARTIFACTS_ROOT = Path("/opt/papertrans/corresponding-source/upstream-archives")
SOURCE_ARTIFACTS_MANIFEST_PATH = (
    SOURCE_ARTIFACTS_ROOT / "source-artifacts.manifest.json"
)
RUNTIME_SOURCE_MAPPING_PATH = COMPLETE_SOURCE_ROOT / "runtime-source-map.json"
FORK_PATCH_PATH = Path(
    "/opt/papertrans/corresponding-source/patches/0001-papertrans-safe-dependencies.patch"
)
RUNTIME_REQUIREMENTS_PATH = Path(
    "/opt/papertrans/corresponding-source/requirements.lock"
)
BUILD_REQUIREMENTS_PATH = Path(
    "/opt/papertrans/corresponding-source/build-requirements.lock"
)
UPSTREAM_LOCK_PATH = Path("/opt/papertrans/corresponding-source/UPSTREAM.lock")

BUILD_MANIFEST_SHA256 = (
    "824659cf4f8d362f0b03088e943e7efd54dc7dd04006dfed83aea75e30863c6b"
)
RUNTIME_REQUIREMENTS_SHA256 = (
    "0fc3f23dc5fa0b0ebb239acaf1b5c24a14cd29b41fe48cfd3d1915dfb832366f"
)
BUILD_REQUIREMENTS_SHA256 = (
    "99c8c09e2be15ac0fa0058f6df36a2dfda624a1c239241c4f59406d7599a0243"
)
FORK_PATCH_SHA256 = "002297dac1447b3ec3e020c4495f7c0b40670677168bdef84d866e6bf296828f"
UPSTREAM_LOCK_SHA256 = (
    "68c5805dfd311f597817643ca0143339e6ba77984a5519b8524a024eea93d9c3"
)
SOURCE_ARTIFACTS_LOCK_SHA256 = (
    "14735922967abea2cf52c177ada33e4a52e0dbd9c7188cb48205c0f3cf334514"
)
BABELDOC_ASSET_INVENTORY_SHA256 = (
    "aed82c0c1fe09f09f3dc5307c646e019948992e37ab62c99e780833220bb9320"
)

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

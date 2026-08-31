from __future__ import annotations

import argparse
import asyncio
import json
import logging
import sys
from pathlib import Path
from typing import Sequence

from .constants import ADAPTER_VERSION
from .constants import ALLOWED_OUTPUTS
from .constants import BACKEND_ID
from .constants import BABELDOC_VERSION
from .constants import ENGINE_VERSION
from .constants import FORK_PATCH_SHA256
from .constants import EXIT_INTERNAL
from .constants import EXIT_INVALID_REQUEST
from .constants import EXIT_OK
from .constants import EXIT_POLICY_REFUSAL
from .constants import INPUT_ROOT
from .constants import OUTPUT_ROOT
from .constants import PROTOCOL_VERSION
from .constants import PROVIDER_SECRET_PATH
from .constants import PYMUPDF_VERSION
from .contract import RUN_ID_RE
from .contract import load_json_object
from .contract import parse_provider_profile
from .contract import parse_request
from .contract import validate_profile_matches_request
from .errors import ContractError
from .errors import PolicyRefusal
from .errors import ResourceLimit
from .errors import WorkerError
from .events import EventEmitter
from .readiness import check_readiness
from .readiness import ReadinessReport
from .runner import execute
from .stdio import duplicate_protocol_stdout


def _parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(prog="papertrans-pdf-worker", allow_abbrev=False)
    subparsers = parser.add_subparsers(dest="command", required=True)
    subparsers.add_parser("health", allow_abbrev=False)
    run = subparsers.add_parser("run", allow_abbrev=False)
    run.add_argument("--request", required=True)
    run.add_argument("--source", required=True)
    run.add_argument("--output", required=True)
    return parser


def _health_document(report: ReadinessReport) -> dict[str, object]:
    return {
        "schemaVersion": PROTOCOL_VERSION,
        "protocolVersion": PROTOCOL_VERSION,
        "backendId": BACKEND_ID,
        "adapterVersion": ADAPTER_VERSION,
        "engineVersion": ENGINE_VERSION,
        "dependencies": {
            "BabelDOC": BABELDOC_VERSION,
            "PyMuPDF": PYMUPDF_VERSION,
        },
        "sourceRevision": report.source_revision or "",
        "forkRevision": FORK_PATCH_SHA256,
        "buildDigest": report.build_digest or "",
        "imageDigest": report.image_digest or "",
        "sbomSha256": report.sbom_sha256 or "",
        "lockSha256": report.lock_sha256 or "",
        "capabilities": {"outputs": sorted(ALLOWED_OUTPUTS)},
        "ready": report.ready,
    }


def _health() -> int:
    protocol_stream = duplicate_protocol_stdout()
    try:
        report = check_readiness()
        document = _health_document(report)
        protocol_stream.write(
            json.dumps(document, ensure_ascii=True, separators=(",", ":")) + "\n"
        )
        protocol_stream.flush()
        return EXIT_OK if report.ready else EXIT_POLICY_REFUSAL
    finally:
        protocol_stream.close()


def _require_path_contract(request_path: Path, source_path: Path, output_path: Path) -> None:
    if request_path != INPUT_ROOT / "request.json" or source_path != INPUT_ROOT / "source.pdf":
        raise ContractError(
            "path_contract_refusal",
            "request and source paths must use the fixed /input contract",
            EXIT_INVALID_REQUEST,
        )
    if output_path != OUTPUT_ROOT:
        raise ContractError(
            "path_contract_refusal",
            "output path must use the fixed /output contract",
            EXIT_INVALID_REQUEST,
        )
    if output_path.is_symlink() or not output_path.is_dir():
        raise ContractError(
            "invalid_output_directory",
            "output must be an existing non-symlink directory",
            EXIT_INVALID_REQUEST,
        )
    try:
        if any(output_path.iterdir()):
            raise ContractError(
                "output_not_empty",
                "output directory must be empty at worker start",
                EXIT_INVALID_REQUEST,
            )
    except OSError as exc:
        raise ContractError(
            "output_unreadable",
            "output directory cannot be inspected",
            EXIT_INVALID_REQUEST,
        ) from exc


def _event_emitter(run_id: str) -> EventEmitter:
    # Keep the protocol on a duplicate of the original stdout fd. The runner
    # can then silence fd 1/2 around untrusted engine code without suppressing
    # normalized worker events.
    return EventEmitter(run_id, duplicate_protocol_stdout())


def _run(args: argparse.Namespace) -> int:
    # The protocol is stdout-only. Third-party logs can contain provider or
    # document details, so retain only normalized worker events.
    logging.disable(logging.CRITICAL)
    request_path = Path(args.request)
    source_path = Path(args.source)
    output_path = Path(args.output)
    run_id = "invalid-request"
    emitter: EventEmitter | None = None
    try:
        _require_path_contract(request_path, source_path, output_path)
        raw_request = load_json_object(request_path)
        candidate_run_id = raw_request.get("runId")
        if isinstance(candidate_run_id, str) and RUN_ID_RE.fullmatch(candidate_run_id):
            run_id = candidate_run_id
        request = parse_request(raw_request)
        run_id = request.run_id
        emitter = _event_emitter(run_id)
        emitter.emit("started", backendId=BACKEND_ID)

        readiness = check_readiness(for_run=True)
        if not readiness.ready:
            failed_checks = [check["name"] for check in readiness.checks if not check["passed"]]
            raise PolicyRefusal(
                "worker_not_ready",
                "worker readiness policy failed: " + ",".join(failed_checks),
                EXIT_POLICY_REFUSAL,
            )
        provider = parse_provider_profile(load_json_object(PROVIDER_SECRET_PATH, max_bytes=16 * 1024))
        validate_profile_matches_request(provider, request)
        asyncio.run(execute(request, source_path, output_path, provider, emitter))
        return EXIT_OK
    except WorkerError as exc:
        if emitter is None:
            emitter = _event_emitter(run_id)
            emitter.emit("started", backendId=BACKEND_ID)
        emitter.emit("failed", code=exc.code, message=exc.public_message)
        return exc.exit_code
    except KeyboardInterrupt:
        if emitter is None:
            emitter = _event_emitter(run_id)
            emitter.emit("started", backendId=BACKEND_ID)
        error = ResourceLimit("cancelled", "translation was cancelled", 6)
        emitter.emit("failed", code=error.code, message=error.public_message)
        return error.exit_code
    except BaseException:
        if emitter is None:
            emitter = _event_emitter(run_id)
            emitter.emit("started", backendId=BACKEND_ID)
        emitter.emit("failed", code="internal_error", message="worker encountered an internal error")
        return EXIT_INTERNAL


def main(argv: Sequence[str] | None = None) -> int:
    args = _parser().parse_args(argv)
    result = _health() if args.command == "health" else _run(args)
    return result


if __name__ == "__main__":
    sys.exit(main())

"""One-command local PaperTrans setup and service launcher."""

from __future__ import annotations

import argparse
import json
import sys
from pathlib import Path
from typing import Sequence

from . import __release__
from .local_runtime import (
    AlreadyRunning,
    LocalSupervisor,
    RuntimeFailure,
    RuntimeOptions,
    ensure_runtime_available,
    probe_mcp,
    probe_web,
    read_runtime_state,
)
from .local_setup import (
    LocalPaths,
    SetupError,
    ensure_setup,
    prepare_directories,
    setup_status,
    validate_repository,
)
from .pdf_import_status import inspect_pdf_import


def _path(value: str) -> Path:
    return Path(value).expanduser()


def _add_path_arguments(parser: argparse.ArgumentParser) -> None:
    parser.add_argument("--data-root", type=_path, help="Data directory (default: <repo>/data)")
    parser.add_argument("--output-root", type=_path, help="Artifact directory (default: <repo>/output)")
    parser.add_argument(
        "--model-root",
        type=_path,
        help="Pinned Docling model directory (default: <data-root>/models/docling)",
    )


def _parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(
        prog="./papertrans",
        description="Prepare and run PaperTrans Web + MCP locally",
    )
    parser.add_argument("--version", action="version", version=f"PaperTrans {__release__}")
    subparsers = parser.add_subparsers(dest="command", required=True)

    start = subparsers.add_parser("start", help="Prepare dependencies, then run Web and MCP")
    _add_path_arguments(start)
    start.add_argument("--offline", action="store_true", help="Use only cached dependencies and models")
    start.add_argument("--dev", action="store_true", help="Run the Next.js development server")
    start.add_argument("--no-browser", action="store_true", help="Do not open the Web library automatically")
    start.add_argument("--web-port", type=int, default=3000)
    start.add_argument("--mcp-port", type=int, default=8000)
    start.add_argument("--startup-timeout", type=float, default=90.0)
    start.add_argument("--shutdown-timeout", type=float, default=8.0)

    setup = subparsers.add_parser("setup", help="Prepare locked dependencies, models, and Web build")
    _add_path_arguments(setup)
    setup.add_argument("--offline", action="store_true", help="Use only cached dependencies and models")
    setup.add_argument("--dev", action="store_true", help="Skip the production Web build")

    doctor = subparsers.add_parser("doctor", help="Check whether local setup is ready")
    _add_path_arguments(doctor)
    doctor.add_argument("--dev", action="store_true", help="Check development-mode readiness")
    doctor.add_argument("--json", action="store_true", help="Print machine-readable JSON")

    status = subparsers.add_parser("status", help="Probe the currently configured local services")
    _add_path_arguments(status)
    status.add_argument("--web-port", type=int, default=3000)
    status.add_argument("--mcp-port", type=int, default=8000)
    status.add_argument("--json", action="store_true", help="Print machine-readable JSON")
    return parser


def _repo_root() -> Path:
    # The POSIX launcher canonicalizes and changes to its own directory.  Do not
    # honor an inherited PAPERTRANS_REPO_ROOT here: only explicit CLI path
    # arguments are accepted for mutable data locations.
    return Path.cwd().resolve()


def _paths(args: argparse.Namespace) -> LocalPaths:
    repo_root = _repo_root()
    data_root = args.data_root
    if data_root is not None and not data_root.is_absolute():
        data_root = repo_root / data_root
    output_root = args.output_root
    if output_root is not None and not output_root.is_absolute():
        output_root = repo_root / output_root
    model_root = args.model_root
    if model_root is not None and not model_root.is_absolute():
        model_root = repo_root / model_root
    return LocalPaths.create(repo_root, data_root, output_root, model_root)


def _print_status(value: dict[str, object]) -> None:
    ready = value.get("ready") is True
    print(f"PaperTrans setup: {'ready' if ready else 'not ready'}")
    for key, label in (
        ("node", "Node.js"),
        ("pnpm", "pnpm"),
        ("python", "Python packages"),
        ("nodeDependencies", "Node dependencies"),
        ("webBuild", "Web build"),
        ("doclingModels", "Docling models"),
    ):
        section = value.get(key)
        if not isinstance(section, dict):
            continue
        marker = "OK" if section.get("ok") is True else "MISSING"
        detail = section.get("detail")
        suffix = f" — {detail}" if isinstance(detail, str) and detail else ""
        print(f"  [{marker}] {label}{suffix}")
        failures = section.get("failures")
        if isinstance(failures, list):
            for failure in failures[:3]:
                print(f"          {failure}")


def _doctor(args: argparse.Namespace) -> int:
    paths = _paths(args)
    validate_repository(paths)
    value = setup_status(paths, dev=args.dev)
    if args.json:
        print(json.dumps(value, ensure_ascii=False, indent=2, sort_keys=True))
    else:
        _print_status(value)
    return 0 if value.get("ready") is True else 1


def _setup(args: argparse.Namespace) -> int:
    paths = _paths(args)
    print("[PaperTrans] Preparing locked local runtime…", flush=True)
    value = ensure_setup(paths, offline=args.offline, dev=args.dev)
    if value.get("ready") is not True:
        _print_status(value)
        raise SetupError("setup finished but one or more readiness checks failed")
    print("[PaperTrans] Setup complete.", flush=True)
    return 0


def _start(args: argparse.Namespace) -> int:
    paths = _paths(args)
    validate_repository(paths)
    prepare_directories(paths)
    ensure_runtime_available(paths)
    value = ensure_setup(paths, offline=args.offline, dev=args.dev)
    if value.get("ready") is not True:
        raise SetupError("setup is incomplete; run ./papertrans doctor for details")
    options = RuntimeOptions(
        web_port=args.web_port,
        mcp_port=args.mcp_port,
        startup_timeout=args.startup_timeout,
        shutdown_timeout=args.shutdown_timeout,
        dev=args.dev,
        no_browser=args.no_browser,
    )
    return LocalSupervisor(paths, options).run()


def _status(args: argparse.Namespace) -> int:
    paths = _paths(args)
    validate_repository(paths)
    prepare_directories(paths)
    options = RuntimeOptions(web_port=args.web_port, mcp_port=args.mcp_port)
    options.validate()
    state = read_runtime_state(paths)
    web_ready = probe_web(args.web_port)
    mcp_ready = probe_mcp(args.mcp_port)
    pdf_import = inspect_pdf_import(paths.output_root)
    value = {
        "ready": web_ready and mcp_ready,
        "safeToModifyArtifacts": (
            not web_ready and not mcp_ready and pdf_import["active"] is False
        ),
        "web": {"ready": web_ready, "url": f"http://127.0.0.1:{args.web_port}/"},
        "mcp": {"ready": mcp_ready, "url": f"http://127.0.0.1:{args.mcp_port}/mcp"},
        "pdfImport": pdf_import,
        "state": state,
    }
    if args.json:
        print(json.dumps(value, ensure_ascii=False, indent=2, sort_keys=True))
    else:
        print(f"PaperTrans services: {'ready' if value['ready'] else 'not ready'}")
        print(f"  Web: {'ready' if value['web']['ready'] else 'stopped'} — {value['web']['url']}")
        print(f"  MCP: {'ready' if value['mcp']['ready'] else 'stopped'} — {value['mcp']['url']}")
        print(
            "  PDF import: "
            f"{pdf_import['state']} — {pdf_import['detail']}"
        )
        print(
            "  Artifact maintenance: "
            f"{'safe' if value['safeToModifyArtifacts'] else 'wait'}"
        )
    return 0 if value["ready"] else 1


def main(argv: Sequence[str] | None = None) -> int:
    arguments = list(sys.argv[1:] if argv is None else argv)
    if not arguments:
        arguments = ["start"]
    elif arguments[0].startswith("-") and arguments[0] not in ("-h", "--help", "--version"):
        arguments.insert(0, "start")
    args = _parser().parse_args(arguments)
    try:
        if args.command == "doctor":
            return _doctor(args)
        if args.command == "setup":
            return _setup(args)
        if args.command == "status":
            return _status(args)
        return _start(args)
    except AlreadyRunning as error:
        print(f"PaperTrans: {error}", file=sys.stderr)
        return 11
    except (SetupError, RuntimeFailure) as error:
        print(f"PaperTrans: {error}", file=sys.stderr)
        return 10
    except KeyboardInterrupt:
        print("\n[PaperTrans] Interrupted.", file=sys.stderr)
        return 130


if __name__ == "__main__":
    raise SystemExit(main())

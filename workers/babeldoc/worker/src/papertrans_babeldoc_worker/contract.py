from __future__ import annotations

import json
import re
from dataclasses import dataclass
from pathlib import Path
from typing import Any
from urllib.parse import urlsplit

from .constants import ALLOWED_OUTPUTS
from .constants import EXIT_INVALID_REQUEST
from .constants import MAX_DEADLINE_SECONDS
from .constants import MAX_INPUT_BYTES
from .constants import MAX_OUTPUT_BYTES
from .constants import MAX_PAGES
from .constants import MAX_REQUEST_BYTES
from .constants import PROTOCOL_VERSION
from .errors import ContractError

RUN_ID_RE = re.compile(r"^[a-z0-9][a-z0-9-]{0,63}$")
TOKEN_RE = re.compile(r"^[A-Za-z0-9][A-Za-z0-9._:/-]{0,127}$")
SHA256_RE = re.compile(r"^[0-9a-f]{64}$")
LANGUAGE_RE = re.compile(r"^[a-z]{2,3}(?:-[A-Z]{2})?$")


def _fail(code: str, message: str) -> ContractError:
    return ContractError(code, message, EXIT_INVALID_REQUEST)


def _object_pairs_no_duplicates(pairs: list[tuple[str, Any]]) -> dict[str, Any]:
    result: dict[str, Any] = {}
    for key, value in pairs:
        if key in result:
            raise _fail("duplicate_json_key", "JSON contains a duplicate object key")
        result[key] = value
    return result


def load_json_object(path: Path, *, max_bytes: int = MAX_REQUEST_BYTES) -> dict[str, Any]:
    try:
        stat = path.lstat()
    except OSError as exc:
        raise _fail("request_unreadable", "JSON input is not a readable regular file") from exc
    if path.is_symlink() or not path.is_file():
        raise _fail("request_not_regular", "JSON input must be a regular non-symlink file")
    if stat.st_size > max_bytes:
        raise _fail("request_too_large", "JSON input exceeds the byte limit")
    try:
        raw = path.read_text(encoding="utf-8")
        value = json.loads(raw, object_pairs_hook=_object_pairs_no_duplicates)
    except ContractError:
        raise
    except (OSError, UnicodeError, json.JSONDecodeError) as exc:
        raise _fail("invalid_json", "JSON input is malformed") from exc
    if not isinstance(value, dict):
        raise _fail("json_not_object", "JSON input must be an object")
    return value


def _exact_keys(value: Any, expected: set[str], where: str) -> dict[str, Any]:
    if not isinstance(value, dict):
        raise _fail("invalid_object", f"{where} must be an object")
    keys = set(value)
    if keys != expected:
        raise _fail("unexpected_fields", f"{where} has missing or unknown fields")
    return value


def _integer(value: Any, where: str, minimum: int, maximum: int) -> int:
    if type(value) is not int or not minimum <= value <= maximum:
        raise _fail("invalid_integer", f"{where} is outside the allowed integer range")
    return value


def _string(value: Any, where: str, pattern: re.Pattern[str]) -> str:
    if not isinstance(value, str) or pattern.fullmatch(value) is None:
        raise _fail("invalid_string", f"{where} has an invalid format")
    return value


@dataclass(frozen=True)
class Source:
    media_type: str
    sha256: str
    bytes: int


@dataclass(frozen=True)
class Translation:
    source_language: str
    target_language: str
    profile_id: str
    provider_id: str
    model_id: str
    prompt_revision: str
    glossary_sha256: str | None


@dataclass(frozen=True)
class Limits:
    max_pages: int
    max_output_bytes: int
    deadline_seconds: int


@dataclass(frozen=True)
class Request:
    schema_version: int
    run_id: str
    source: Source
    translation: Translation
    outputs: tuple[str, ...]
    limits: Limits


def parse_request(value: dict[str, Any]) -> Request:
    root = _exact_keys(
        value,
        {"schemaVersion", "runId", "source", "translation", "outputs", "limits"},
        "request",
    )
    if root["schemaVersion"] != PROTOCOL_VERSION or type(root["schemaVersion"]) is not int:
        raise _fail("unsupported_schema", "schemaVersion is not supported")
    run_id = _string(root["runId"], "runId", RUN_ID_RE)

    source_obj = _exact_keys(root["source"], {"mediaType", "sha256", "bytes"}, "source")
    if source_obj["mediaType"] != "application/pdf":
        raise _fail("unsupported_media_type", "source.mediaType must be application/pdf")
    source = Source(
        media_type="application/pdf",
        sha256=_string(source_obj["sha256"], "source.sha256", SHA256_RE),
        bytes=_integer(source_obj["bytes"], "source.bytes", 1, MAX_INPUT_BYTES),
    )

    translation_obj = _exact_keys(
        root["translation"],
        {
            "sourceLanguage",
            "targetLanguage",
            "profileId",
            "providerId",
            "modelId",
            "promptRevision",
            "glossarySha256",
        },
        "translation",
    )
    source_language = _string(
        translation_obj["sourceLanguage"], "translation.sourceLanguage", LANGUAGE_RE
    )
    target_language = _string(
        translation_obj["targetLanguage"], "translation.targetLanguage", LANGUAGE_RE
    )
    if (source_language, target_language) != ("en", "ja"):
        raise _fail("unsupported_language_pair", "only the evaluated en-to-ja profile is allowed")
    profile_id = _string(translation_obj["profileId"], "translation.profileId", RUN_ID_RE)
    provider_id = _string(translation_obj["providerId"], "translation.providerId", RUN_ID_RE)
    model_id = _string(translation_obj["modelId"], "translation.modelId", TOKEN_RE)
    prompt_revision = _string(
        translation_obj["promptRevision"], "translation.promptRevision", RUN_ID_RE
    )
    if profile_id != "evaluation-ja-v1" or prompt_revision != "papertrans-pdf-ja-v1":
        raise _fail("unapproved_profile", "translation profile or prompt revision is not approved")
    glossary_sha256 = translation_obj["glossarySha256"]
    if glossary_sha256 is not None:
        raise _fail("unsupported_glossary", "this worker build does not accept a glossary")
    translation = Translation(
        source_language=source_language,
        target_language=target_language,
        profile_id=profile_id,
        provider_id=provider_id,
        model_id=model_id,
        prompt_revision=prompt_revision,
        glossary_sha256=None,
    )

    outputs_value = root["outputs"]
    if not isinstance(outputs_value, list) or not outputs_value:
        raise _fail("invalid_outputs", "outputs must be a non-empty array")
    if any(not isinstance(item, str) or item not in ALLOWED_OUTPUTS for item in outputs_value):
        raise _fail("unsupported_output", "outputs contains an unsupported role")
    if len(outputs_value) != len(set(outputs_value)):
        raise _fail("duplicate_output", "outputs contains a duplicate role")

    limits_obj = _exact_keys(
        root["limits"], {"maxPages", "maxOutputBytes", "deadlineSeconds"}, "limits"
    )
    limits = Limits(
        max_pages=_integer(limits_obj["maxPages"], "limits.maxPages", 1, MAX_PAGES),
        max_output_bytes=_integer(
            limits_obj["maxOutputBytes"], "limits.maxOutputBytes", 1, MAX_OUTPUT_BYTES
        ),
        deadline_seconds=_integer(
            limits_obj["deadlineSeconds"],
            "limits.deadlineSeconds",
            1,
            MAX_DEADLINE_SECONDS,
        ),
    )
    return Request(
        schema_version=PROTOCOL_VERSION,
        run_id=run_id,
        source=source,
        translation=translation,
        outputs=tuple(outputs_value),
        limits=limits,
    )


@dataclass(frozen=True)
class ProviderProfile:
    profile_id: str
    provider_id: str
    model_id: str
    base_url: str
    api_key: str
    qps: int


def parse_provider_profile(value: dict[str, Any]) -> ProviderProfile:
    obj = _exact_keys(
        value,
        {"schemaVersion", "profileId", "providerId", "modelId", "baseUrl", "apiKey", "qps"},
        "provider profile",
    )
    if obj["schemaVersion"] != 1 or type(obj["schemaVersion"]) is not int:
        raise _fail("unsupported_provider_schema", "provider schemaVersion is not supported")
    profile_id = _string(obj["profileId"], "provider.profileId", RUN_ID_RE)
    provider_id = _string(obj["providerId"], "provider.providerId", RUN_ID_RE)
    model_id = _string(obj["modelId"], "provider.modelId", TOKEN_RE)
    if not isinstance(obj["baseUrl"], str) or len(obj["baseUrl"]) > 2048:
        raise _fail("invalid_provider_url", "provider.baseUrl has an invalid format")
    parsed_url = urlsplit(obj["baseUrl"])
    if (
        parsed_url.scheme not in {"http", "https"}
        or not parsed_url.hostname
        or parsed_url.username is not None
        or parsed_url.password is not None
        or parsed_url.query
        or parsed_url.fragment
    ):
        raise _fail("invalid_provider_url", "provider.baseUrl must be an origin/path without credentials")
    api_key = obj["apiKey"]
    if not isinstance(api_key, str) or not api_key or len(api_key) > 4096 or any(
        char in api_key for char in "\r\n\x00"
    ):
        raise _fail("invalid_provider_key", "provider.apiKey has an invalid format")
    qps = _integer(obj["qps"], "provider.qps", 1, 8)
    return ProviderProfile(profile_id, provider_id, model_id, obj["baseUrl"].rstrip("/"), api_key, qps)


def validate_profile_matches_request(profile: ProviderProfile, request: Request) -> None:
    if (
        profile.profile_id != request.translation.profile_id
        or profile.provider_id != request.translation.provider_id
        or profile.model_id != request.translation.model_id
    ):
        raise _fail("provider_profile_mismatch", "request does not match the mounted provider profile")

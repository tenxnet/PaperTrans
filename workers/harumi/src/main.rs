use async_trait::async_trait;
use chrono::{SecondsFormat, Utc};
use harumi::Document;
use harumi_ai::{
    Error as HarumiAiError, LayoutRepairMode, QualityProfile, QualityResult,
    Result as HarumiResult, TranslateOptions, TranslateOutput, TranslationMode, Translator,
    translate_pdf,
};
use serde::{Deserialize, Serialize};
use serde_json::{Value, json};
use sha2::{Digest, Sha256};
use std::env;
use std::fmt;
use std::fs::{self, File, OpenOptions};
use std::io::{self, Read, Write};
use std::path::{Path, PathBuf};
use std::process::ExitCode;
use std::sync::{Arc, Mutex};
use std::time::{Duration, Instant};

const WORKER_NAME: &str = "papertrans-harumi-worker";
const BACKEND_ID: &str = "papertrans-harumi";
const HARUMI_VERSION: &str = "1.19.0";
const HARUMI_AI_VERSION: &str = "0.9.0";
const SCHEMA_VERSION: u64 = 1;
const PURPOSE: &str = "layout_evaluation_only";
const PROFILE_ID: &str = "harumi-layout-eval-ja-v1";
const PROVIDER_ID: &str = "deterministic-local";
const MODEL_ID: &str = "deterministic-layout-v1";
const PROMPT_REVISION: &str = "papertrans-pdf-layout-v1";
const DEFAULT_FONT_PATH: &str = "/assets/NotoSansJP-wght.ttf";
const PDF_ARTIFACT_PATH: &str = "artifacts/translated-mono.pdf";
const REPORT_ARTIFACT_PATH: &str = "artifacts/backend-report.json";
const RESULT_PATH: &str = "worker-result.json";
const MAX_REQUEST_BYTES: u64 = 64 * 1024;
const MAX_SOURCE_BYTES: u64 = 100 * 1024 * 1024;
const MAX_FONT_BYTES: u64 = 32 * 1024 * 1024;
const MAX_PAGES: u32 = 300;
const MAX_OUTPUT_BYTES: u64 = 500 * 1024 * 1024;
const MAX_DEADLINE_SECONDS: u64 = 1_500;

#[derive(Debug)]
struct WorkerError {
    code: &'static str,
    message: String,
    exit_code: u8,
}

impl WorkerError {
    fn new(code: &'static str, message: impl Into<String>, exit_code: u8) -> Self {
        Self {
            code,
            message: sanitize_message(&message.into()),
            exit_code,
        }
    }

    fn invalid(code: &'static str, message: impl Into<String>) -> Self {
        Self::new(code, message, 2)
    }

    fn policy(code: &'static str, message: impl Into<String>) -> Self {
        Self::new(code, message, 3)
    }

    fn provider(code: &'static str, message: impl Into<String>) -> Self {
        Self::new(code, message, 4)
    }

    fn pdf(code: &'static str, message: impl Into<String>) -> Self {
        Self::new(code, message, 5)
    }

    fn resource(code: &'static str, message: impl Into<String>) -> Self {
        Self::new(code, message, 6)
    }

    fn internal(code: &'static str, message: impl Into<String>) -> Self {
        Self::new(code, message, 70)
    }
}

impl fmt::Display for WorkerError {
    fn fmt(&self, f: &mut fmt::Formatter<'_>) -> fmt::Result {
        f.write_str(&self.message)
    }
}

impl std::error::Error for WorkerError {}

impl From<io::Error> for WorkerError {
    fn from(error: io::Error) -> Self {
        Self::internal("io_error", error.to_string())
    }
}

impl From<HarumiAiError> for WorkerError {
    fn from(error: HarumiAiError) -> Self {
        match error {
            HarumiAiError::Translator(message) => {
                Self::provider("translation_provider_failed", message)
            }
            HarumiAiError::LengthMismatch { expected, got } => Self::provider(
                "translation_cardinality_mismatch",
                format!("translator returned {got} items for {expected} inputs"),
            ),
            HarumiAiError::Harumi(error) => {
                Self::pdf("pdf_backend_failed", error.to_string())
            }
            HarumiAiError::FontParse(_) => Self::policy(
                "trusted_font_invalid",
                "the configured trusted font could not be parsed",
            ),
            HarumiAiError::Io(error) => Self::internal("backend_io_failed", error.to_string()),
            HarumiAiError::QualityGateFailed(violations) => Self::pdf(
                "quality_gate_failed",
                format!(
                    "harumi quality profile rejected {} layout issue(s)",
                    violations.len()
                ),
            ),
            _ => Self::internal("harumi_backend_failed", "unclassified harumi backend error"),
        }
    }
}

type WorkerResult<T> = Result<T, WorkerError>;

#[derive(Default)]
struct EventState {
    next_sequence: u64,
}

#[derive(Clone, Default)]
struct EventSink {
    state: Arc<Mutex<EventState>>,
}

impl EventSink {
    fn emit_document(&self, document: &Value) {
        let Ok(_guard) = self.state.lock() else {
            return;
        };
        write_ndjson_value(document);
    }

    fn emit_run(&self, run_id: &str, event_type: &'static str, payload: Value) {
        let Ok(mut state) = self.state.lock() else {
            return;
        };
        state.next_sequence = state.next_sequence.saturating_add(1);
        let mut event = json!({
            "schemaVersion": SCHEMA_VERSION,
            "runId": run_id,
            "sequence": state.next_sequence,
            "time": rfc3339_now(),
            "type": event_type,
        });
        if let (Some(event_object), Value::Object(payload_object)) =
            (event.as_object_mut(), payload)
        {
            event_object.extend(payload_object);
        }
        write_ndjson_value(&event);
    }
}

fn write_ndjson_value(value: &Value) {
    let stdout = io::stdout();
    let mut out = stdout.lock();
    if serde_json::to_writer(&mut out, value).is_ok() {
        let _ = out.write_all(b"\n");
        let _ = out.flush();
    }
}

#[derive(Debug)]
enum Command {
    Health,
    Run(RunArgs),
}

#[derive(Debug)]
struct RunArgs {
    request: PathBuf,
    source: PathBuf,
    output: PathBuf,
}

#[derive(Debug, Deserialize)]
#[serde(rename_all = "camelCase", deny_unknown_fields)]
struct RunRequest {
    schema_version: u64,
    run_id: String,
    source: SourceRequest,
    translation: TranslationRequest,
    outputs: Vec<RequestedOutput>,
    limits: LimitsRequest,
}

#[derive(Debug, Deserialize)]
#[serde(rename_all = "camelCase", deny_unknown_fields)]
struct SourceRequest {
    media_type: String,
    sha256: String,
    bytes: u64,
}

#[derive(Debug, Deserialize)]
#[serde(rename_all = "camelCase", deny_unknown_fields)]
struct TranslationRequest {
    source_language: String,
    target_language: String,
    profile_id: String,
    provider_id: String,
    model_id: String,
    prompt_revision: String,
    glossary_sha256: NullableSha256,
}

#[derive(Debug, Deserialize)]
#[serde(transparent)]
struct NullableSha256(Option<String>);

#[derive(Debug, Deserialize)]
#[serde(deny_unknown_fields)]
struct BatchInput {
    pages: Vec<BatchInputPage>,
}

#[derive(Debug, Deserialize)]
#[serde(deny_unknown_fields)]
struct BatchInputPage {
    #[serde(rename = "page")]
    _page: u32,
    blocks: Vec<BatchInputBlock>,
}

#[derive(Debug, Deserialize)]
#[serde(deny_unknown_fields)]
struct BatchInputBlock {
    id: usize,
    #[serde(rename = "type")]
    _block_type: String,
    text: String,
}

#[derive(Debug, Serialize)]
struct BatchOutput {
    pages: Vec<BatchOutputPage>,
}

#[derive(Debug, Serialize)]
struct BatchOutputPage {
    blocks: Vec<BatchOutputBlock>,
}

#[derive(Debug, Serialize)]
struct BatchOutputBlock {
    id: usize,
    text: String,
}

#[derive(Debug, Default)]
struct BatchStats {
    segments: u64,
    source_characters: u64,
    output_characters: u64,
}

#[derive(Debug, Deserialize, PartialEq, Eq)]
#[serde(rename_all = "snake_case")]
enum RequestedOutput {
    TranslatedMonoPdf,
    TranslatedDualPdf,
}

#[derive(Debug, Deserialize)]
#[serde(rename_all = "camelCase", deny_unknown_fields)]
struct LimitsRequest {
    max_pages: u32,
    max_output_bytes: u64,
    deadline_seconds: u64,
}

#[derive(Debug, Default, Clone, Serialize)]
#[serde(rename_all = "camelCase")]
struct TranslationStats {
    calls: u64,
    segments: u64,
    source_characters: u64,
    output_characters: u64,
}

#[derive(Clone, Default)]
struct DeterministicJapaneseLayoutTranslator {
    stats: Arc<Mutex<TranslationStats>>,
}

impl DeterministicJapaneseLayoutTranslator {
    fn snapshot(&self) -> WorkerResult<TranslationStats> {
        self.stats
            .lock()
            .map(|stats| stats.clone())
            .map_err(|_| {
                WorkerError::internal(
                    "translator_state_poisoned",
                    "translator state lock failed",
                )
            })
    }
}

#[async_trait]
impl Translator for DeterministicJapaneseLayoutTranslator {
    async fn translate(
        &self,
        texts: &[String],
        _target_lang: &str,
        _source_lang: Option<&str>,
    ) -> HarumiResult<Vec<String>> {
        let mut translated = Vec::with_capacity(texts.len());
        let mut batch_stats = BatchStats::default();
        for raw_batch in texts {
            let (translated_batch, stats) = translate_batch_json(raw_batch)?;
            translated.push(translated_batch);
            batch_stats.segments = batch_stats.segments.saturating_add(stats.segments);
            batch_stats.source_characters = batch_stats
                .source_characters
                .saturating_add(stats.source_characters);
            batch_stats.output_characters = batch_stats
                .output_characters
                .saturating_add(stats.output_characters);
        }
        let mut stats = self
            .stats
            .lock()
            .map_err(|_| HarumiAiError::Translator("translator state lock failed".to_owned()))?;
        stats.calls = stats.calls.saturating_add(1);
        stats.segments = stats.segments.saturating_add(batch_stats.segments);
        stats.source_characters = stats
            .source_characters
            .saturating_add(batch_stats.source_characters);
        stats.output_characters = stats
            .output_characters
            .saturating_add(batch_stats.output_characters);
        drop(stats);
        Ok(translated)
    }
}

fn translate_batch_json(raw: &str) -> HarumiResult<(String, BatchStats)> {
    let input: BatchInput = serde_json::from_str(raw).map_err(|_| {
        HarumiAiError::Translator(
            "translator input did not match the expected Harumi batch JSON".to_owned(),
        )
    })?;
    if input.pages.is_empty() {
        return Err(HarumiAiError::Translator(
            "translator input contained no pages".to_owned(),
        ));
    }

    let mut stats = BatchStats::default();
    let mut pages = Vec::with_capacity(input.pages.len());
    for page in input.pages {
        let mut blocks = Vec::with_capacity(page.blocks.len());
        for block in page.blocks {
            let translated = deterministic_layout_text(&block.text);
            stats.segments = stats.segments.saturating_add(1);
            stats.source_characters = stats
                .source_characters
                .saturating_add(block.text.chars().count() as u64);
            stats.output_characters = stats
                .output_characters
                .saturating_add(translated.chars().count() as u64);
            blocks.push(BatchOutputBlock {
                id: block.id,
                text: translated,
            });
        }
        pages.push(BatchOutputPage { blocks });
    }
    let encoded = serde_json::to_string(&BatchOutput { pages }).map_err(|_| {
        HarumiAiError::Translator("translator output serialization failed".to_owned())
    })?;
    Ok((encoded, stats))
}

#[derive(Debug, Serialize)]
#[serde(rename_all = "camelCase")]
struct PageQuality {
    page_number: u32,
    overflow_count: usize,
    collision_count: usize,
    shrunk_count: usize,
    worst_overlap_area: f32,
    issue_count: usize,
    unresolved_issue_count: usize,
}

#[derive(Debug, Default, Serialize)]
#[serde(rename_all = "camelCase")]
struct QualityTotals {
    pages: usize,
    overflow_count: usize,
    collision_count: usize,
    shrunk_count: usize,
    issue_count: usize,
    unresolved_issue_count: usize,
    worst_overlap_area: f32,
}

struct Readiness {
    document: Value,
    failures: Vec<&'static str>,
}

#[derive(Debug)]
struct RunFailure {
    run_id: Option<String>,
    error: WorkerError,
}

#[tokio::main(flavor = "current_thread")]
async fn main() -> ExitCode {
    let sink = EventSink::default();
    let command = match parse_command(env::args().skip(1)) {
        Ok(command) => command,
        Err(error) => {
            eprintln!("{WORKER_NAME}: {}", error.code);
            return ExitCode::from(error.exit_code);
        }
    };

    match command {
        Command::Health => {
            let readiness = readiness();
            sink.emit_document(&readiness.document);
            if readiness.failures.is_empty() {
                ExitCode::SUCCESS
            } else {
                eprintln!(
                    "{WORKER_NAME}: readiness failed: {}",
                    readiness.failures.join(",")
                );
                ExitCode::from(3)
            }
        }
        Command::Run(args) => match run(args, &sink).await {
            Ok(()) => ExitCode::SUCCESS,
            Err(failure) => {
                if let Some(run_id) = failure.run_id.as_deref() {
                    sink.emit_run(
                        run_id,
                        "failed",
                        json!({
                            "code": failure.error.code,
                            "message": failure.error.message,
                        }),
                    );
                } else {
                    eprintln!("{WORKER_NAME}: {}", failure.error.code);
                }
                ExitCode::from(failure.error.exit_code)
            }
        },
    }
}

fn parse_command(mut args: impl Iterator<Item = String>) -> WorkerResult<Command> {
    let command = args
        .next()
        .ok_or_else(|| WorkerError::invalid("invalid_cli", "expected `health` or `run`"))?;
    match command.as_str() {
        "health" => {
            if args.next().is_some() {
                return Err(WorkerError::invalid(
                    "invalid_cli",
                    "`health` does not accept arguments",
                ));
            }
            Ok(Command::Health)
        }
        "run" => {
            let mut request = None;
            let mut source = None;
            let mut output = None;
            while let Some(flag) = args.next() {
                let value = args.next().ok_or_else(|| {
                    WorkerError::invalid("invalid_cli", format!("missing value for `{flag}`"))
                })?;
                let slot = match flag.as_str() {
                    "--request" => &mut request,
                    "--source" => &mut source,
                    "--output" => &mut output,
                    _ => {
                        return Err(WorkerError::invalid(
                            "invalid_cli",
                            format!("unknown option `{flag}`"),
                        ));
                    }
                };
                if slot.replace(PathBuf::from(value)).is_some() {
                    return Err(WorkerError::invalid(
                        "invalid_cli",
                        format!("duplicate option `{flag}`"),
                    ));
                }
            }
            Ok(Command::Run(RunArgs {
                request: request.ok_or_else(|| {
                    WorkerError::invalid("invalid_cli", "missing required `--request`")
                })?,
                source: source.ok_or_else(|| {
                    WorkerError::invalid("invalid_cli", "missing required `--source`")
                })?,
                output: output.ok_or_else(|| {
                    WorkerError::invalid("invalid_cli", "missing required `--output`")
                })?,
            }))
        }
        _ => Err(WorkerError::invalid(
            "invalid_cli",
            format!("unknown command `{command}`"),
        )),
    }
}

async fn run(args: RunArgs, sink: &EventSink) -> Result<(), RunFailure> {
    let request = read_request(&args.request).map_err(|error| RunFailure {
        run_id: None,
        error,
    })?;
    validate_request(&request).map_err(|error| RunFailure {
        run_id: None,
        error,
    })?;
    let run_id = Some(request.run_id.clone());
    run_with_request(args, request, sink)
        .await
        .map_err(|error| RunFailure { run_id, error })
}

async fn run_with_request(
    args: RunArgs,
    request: RunRequest,
    sink: &EventSink,
) -> WorkerResult<()> {
    let started = Instant::now();
    sink.emit_run(
        &request.run_id,
        "started",
        json!({"backendId": BACKEND_ID}),
    );
    let readiness = readiness();
    if !readiness.failures.is_empty() {
        return Err(WorkerError::policy(
            "worker_not_ready",
            format!(
                "worker readiness failed: {}",
                readiness.failures.join(",")
            ),
        ));
    }

    let source = read_limited_file(&args.source, MAX_SOURCE_BYTES, "source PDF")?;
    if !source.starts_with(b"%PDF-") {
        return Err(WorkerError::pdf(
            "unsupported_input",
            "source does not begin with a PDF header",
        ));
    }
    if source.len() as u64 != request.source.bytes {
        return Err(WorkerError::policy(
            "source_size_mismatch",
            "source byte count does not match the host-owned request",
        ));
    }
    let source_sha256 = sha256_hex(&source);
    if source_sha256 != request.source.sha256 {
        return Err(WorkerError::policy(
            "source_digest_mismatch",
            "source digest does not match the host-owned request",
        ));
    }
    let source_document = Document::from_bytes(&source)
        .map_err(|error| WorkerError::pdf("unsupported_input", error.to_string()))?;
    if source_document.is_encrypted() {
        return Err(WorkerError::pdf(
            "unsupported_input",
            "encrypted PDFs are not supported",
        ));
    }
    let source_page_count = source_document.page_count();
    if source_page_count == 0 {
        return Err(WorkerError::pdf(
            "unsupported_input",
            "source PDF has no pages",
        ));
    }
    if source_page_count > request.limits.max_pages {
        return Err(WorkerError::resource(
            "page_limit_exceeded",
            "source page count exceeds limits.maxPages",
        ));
    }
    drop(source_document);

    let font_path = trusted_font_path()?;
    let font = read_limited_file(&font_path, MAX_FONT_BYTES, "trusted CJK font")?;
    let font_sha256 = sha256_hex(&font);
    verify_expected_font_hash(&font_sha256)?;
    prepare_output_dir(&args.output)?;

    let translator = DeterministicJapaneseLayoutTranslator::default();
    let translator_for_stats = translator.clone();
    let progress_sink = sink.clone();
    let progress_run_id = request.run_id.clone();
    let options = TranslateOptions::builder()
        .target_lang(request.translation.target_language.clone())
        .source_lang(request.translation.source_language.clone())
        .translator(translator)
        .font(font.clone())
        .mode(TranslationMode::Overlay)
        // This adapter is an explicitly non-promotable layout evaluation.  A
        // permissive backend gate lets the host retain the PDF plus Harumi's
        // full per-page diagnostics for independent visual comparison.
        .profile(QualityProfile::BestEffort)
        .layout_repair_mode(LayoutRepairMode::GeometryOnly)
        .max_correction_rounds(0)
        .auto_skip_math(true)
        .concurrency(1)
        .pages_per_batch(1)
        .on_progress(move |completed, total| {
            progress_sink.emit_run(
                &progress_run_id,
                "progress",
                json!({
                    "stage": "translate",
                    "completed": completed,
                    "total": total,
                }),
            );
        })
        .build();

    sink.emit_run(
        &request.run_id,
        "stage",
        json!({"stage": "translate"}),
    );
    let deadline = Duration::from_secs(request.limits.deadline_seconds);
    let remaining = deadline.checked_sub(started.elapsed()).ok_or_else(|| {
        WorkerError::resource("deadline_exceeded", "worker deadline elapsed before translation")
    })?;
    let output = tokio::time::timeout(remaining, translate_pdf(&source, options))
        .await
        .map_err(|_| {
            WorkerError::resource("deadline_exceeded", "harumi translation exceeded the deadline")
        })??;

    if !output.pdf_bytes.starts_with(b"%PDF-") {
        return Err(WorkerError::pdf(
            "invalid_backend_pdf",
            "harumi output does not begin with a PDF header",
        ));
    }
    let output_document = Document::from_bytes(&output.pdf_bytes)
        .map_err(|error| WorkerError::pdf("invalid_backend_pdf", error.to_string()))?;
    if output_document.page_count() != source_page_count {
        return Err(WorkerError::pdf(
            "page_count_mismatch",
            "monolingual output did not preserve source page count",
        ));
    }
    drop(output_document);

    let translation_stats = translator_for_stats.snapshot()?;
    if translation_stats.segments == 0 {
        return Err(WorkerError::pdf(
            "unsupported_input",
            "no translatable digital text was found; OCR input is not supported",
        ));
    }
    let (pages, totals) = summarize_quality(&output);
    let page_map: Vec<Value> = (1..=source_page_count)
        .map(|page| {
            json!({
                "sourcePage": page,
                "outputPages": [page],
            })
        })
        .collect();
    let pdf_sha256 = sha256_hex(&output.pdf_bytes);
    let pdf_bytes = output.pdf_bytes.len() as u64;
    let per_pdf_limit = request
        .limits
        .max_output_bytes
        .min(request.source.bytes.saturating_mul(5));
    if pdf_bytes > per_pdf_limit {
        return Err(WorkerError::resource(
            "output_limit_exceeded",
            "translated PDF exceeds five times source size or limits.maxOutputBytes",
        ));
    }

    let report = json!({
        "schemaVersion": SCHEMA_VERSION,
        "runId": request.run_id,
        "backendId": BACKEND_ID,
        "evidenceClass": "untrusted_backend_supporting_evidence",
        "backend": {
            "harumiVersion": HARUMI_VERSION,
            "harumiAiVersion": HARUMI_AI_VERSION,
            "translationMode": "overlay",
            "qualityProfile": "best_effort",
            "layoutRepairMode": "geometry_only",
            "autoSkipMath": true,
            "visionProvider": false,
            "popplerUsed": false,
            "modeUsed": translation_mode_name(&output.quality.mode_used),
            "fallbackReason": output.quality.fallback_reason.as_deref(),
            "correctionRounds": output.quality.correction_rounds,
        },
        "evaluation": {
            "purpose": PURPOSE,
            "promotionEligible": false,
            "strategy": MODEL_ID,
            "semanticTranslation": false,
            "disclaimer": "Deterministic Japanese placeholder text for layout evaluation; it is not a semantic translation.",
            "translationStats": translation_stats,
        },
        "quality": {
            "overall": quality_result_name(&output.quality.overall),
            "passed": output.quality.overall.is_pass(),
            "totals": totals,
            "pages": pages,
        },
        "pageMaps": {
            "translated_mono_pdf": page_map,
        },
        "font": {
            "fileName": font_path.file_name().and_then(|name| name.to_str()),
            "sha256": font_sha256,
            "bytes": font.len() as u64,
        },
        "durationMs": started.elapsed().as_millis() as u64,
    });
    let report_bytes = serde_json::to_vec_pretty(&report).map_err(|_| {
        WorkerError::internal("result_serialization_failed", "could not serialize backend report")
    })?;
    let report_sha256 = sha256_hex(&report_bytes);
    let report_size = report_bytes.len() as u64;
    if pdf_bytes.saturating_add(report_size) > request.limits.max_output_bytes {
        return Err(WorkerError::resource(
            "output_limit_exceeded",
            "aggregate worker artifacts exceed limits.maxOutputBytes",
        ));
    }

    let result = json!({
        "schemaVersion": SCHEMA_VERSION,
        "runId": request.run_id,
        "sourceSha256": source_sha256,
        "artifacts": [
            {
                "role": "translated_mono_pdf",
                "path": PDF_ARTIFACT_PATH,
                "mediaType": "application/pdf",
                "sha256": pdf_sha256,
                "bytes": pdf_bytes,
            },
            {
                "role": "backend_report",
                "path": REPORT_ARTIFACT_PATH,
                "mediaType": "application/json",
                "sha256": report_sha256,
                "bytes": report_size,
            }
        ],
        "pageMaps": {
            "translated_mono_pdf": page_map,
        },
    });
    let result_bytes = serde_json::to_vec_pretty(&result).map_err(|_| {
        WorkerError::internal("result_serialization_failed", "could not serialize worker result")
    })?;

    let pdf_path = args.output.join(PDF_ARTIFACT_PATH);
    let report_path = args.output.join(REPORT_ARTIFACT_PATH);
    let result_path = args.output.join(RESULT_PATH);
    write_new_atomic(&pdf_path, &output.pdf_bytes)?;
    write_new_atomic(&report_path, &report_bytes)?;
    write_new_atomic(&result_path, &result_bytes)?;

    sink.emit_run(
        &request.run_id,
        "artifact",
        json!({
            "role": "translated_mono_pdf",
            "path": PDF_ARTIFACT_PATH,
            "mediaType": "application/pdf",
            "sha256": pdf_sha256,
            "bytes": pdf_bytes,
        }),
    );
    sink.emit_run(
        &request.run_id,
        "artifact",
        json!({
            "role": "backend_report",
            "path": REPORT_ARTIFACT_PATH,
            "mediaType": "application/json",
            "sha256": report_sha256,
            "bytes": report_size,
        }),
    );
    sink.emit_run(&request.run_id, "completed", json!({}));
    Ok(())
}

fn readiness() -> Readiness {
    let (source_revision, source_ok) = required_env(
        "PAPERTRANS_WORKER_SOURCE_REVISION",
        valid_lower_sha256,
    );
    let (build_digest, build_ok) =
        required_env("PAPERTRANS_WORKER_BUILD_DIGEST", valid_image_digest);
    let (image_digest, image_ok) =
        required_env("PAPERTRANS_WORKER_IMAGE_DIGEST", valid_image_digest);
    let (sbom_sha256, sbom_ok) =
        required_env("PAPERTRANS_WORKER_SBOM_SHA256", valid_lower_sha256);
    let (lock_sha256, lock_ok) =
        required_env("PAPERTRANS_WORKER_LOCK_SHA256", valid_lower_sha256);
    let (font_sha256, font_ok) =
        required_env("PAPERTRANS_HARUMI_FONT_SHA256", valid_lower_sha256);
    let mut failures = Vec::new();
    for (name, passed) in [
        ("source_revision", source_ok),
        ("build_digest", build_ok),
        ("image_digest", image_ok),
        ("sbom_sha256", sbom_ok),
        ("lock_sha256", lock_ok),
        ("font_sha256", font_ok),
    ] {
        if !passed {
            failures.push(name);
        }
    }
    let font_digests = if font_ok {
        json!({"noto-cjk-ja": font_sha256})
    } else {
        json!({})
    };
    let document = json!({
        "schemaVersion": SCHEMA_VERSION,
        "protocolVersion": SCHEMA_VERSION,
        "backendId": BACKEND_ID,
        "adapterVersion": env!("CARGO_PKG_VERSION"),
        "engineVersion": HARUMI_VERSION,
        "dependencies": {
            "harumi": HARUMI_VERSION,
            "harumi-ai": HARUMI_AI_VERSION,
        },
        "sourceRevision": source_revision,
        "forkRevision": null,
        "buildDigest": build_digest,
        "imageDigest": image_digest,
        "sbomSha256": sbom_sha256,
        "lockSha256": lock_sha256,
        "capabilities": {
            "outputs": ["translated_mono_pdf"],
        },
        "ready": failures.is_empty(),
        "fontDigests": font_digests,
    });
    Readiness { document, failures }
}

fn required_env(name: &str, validator: fn(&str) -> bool) -> (String, bool) {
    match env::var(name) {
        Ok(value) if validator(&value) => (value, true),
        _ => (String::new(), false),
    }
}

fn deterministic_layout_text(source: &str) -> String {
    const FILLER: &[char] = &[
        '配', '置', '評', '価', '用', '日', '本', '語', '文', '字', '組', '版', '確', '認',
    ];
    let source_chars = source.chars().count();
    let target_chars = source_chars.saturating_mul(45).div_ceil(100).max(2);
    let digest = Sha256::digest(source.as_bytes());
    let offset = digest[0] as usize % FILLER.len();
    let mut chars = Vec::with_capacity(target_chars);
    chars.extend(['仮', '訳']);
    for index in 0..target_chars.saturating_sub(chars.len()) {
        chars.push(FILLER[(offset + index) % FILLER.len()]);
    }
    chars.into_iter().collect()
}

fn read_request(path: &Path) -> WorkerResult<RunRequest> {
    let bytes = read_limited_file(path, MAX_REQUEST_BYTES, "request JSON")?;
    serde_json::from_slice(&bytes).map_err(|_| {
        WorkerError::invalid(
            "invalid_request",
            "request JSON does not match the strict PaperTrans PDF worker schema",
        )
    })
}

fn validate_request(request: &RunRequest) -> WorkerResult<()> {
    if request.schema_version != SCHEMA_VERSION {
        return Err(WorkerError::invalid(
            "unsupported_schema_version",
            format!("schemaVersion must be {SCHEMA_VERSION}"),
        ));
    }
    if !valid_run_id(&request.run_id) {
        return Err(WorkerError::invalid(
            "invalid_run_id",
            "runId must match ^[a-z0-9][a-z0-9-]{0,63}$",
        ));
    }
    if request.source.media_type != "application/pdf" {
        return Err(WorkerError::invalid(
            "unsupported_media_type",
            "source.mediaType must be application/pdf",
        ));
    }
    if !valid_lower_sha256(&request.source.sha256) {
        return Err(WorkerError::invalid(
            "invalid_source_digest",
            "source.sha256 must be 64 lowercase hexadecimal characters",
        ));
    }
    if request.source.bytes == 0 || request.source.bytes > MAX_SOURCE_BYTES {
        return Err(WorkerError::resource(
            "source_size_limit",
            "source.bytes must be within the 100 MiB worker limit",
        ));
    }
    if request.outputs.len() != 1
        || request.outputs.first() != Some(&RequestedOutput::TranslatedMonoPdf)
    {
        return Err(WorkerError::invalid(
            "unsupported_output",
            "Harumi layout evaluation requires outputs=[\"translated_mono_pdf\"]",
        ));
    }
    if request.translation.source_language != "en"
        || request.translation.target_language != "ja"
    {
        return Err(WorkerError::invalid(
            "unsupported_language_pair",
            "Harumi Phase 1 layout evaluation requires en to ja",
        ));
    }
    if request.translation.profile_id != PROFILE_ID
        || request.translation.provider_id != PROVIDER_ID
        || request.translation.model_id != MODEL_ID
        || request.translation.prompt_revision != PROMPT_REVISION
    {
        return Err(WorkerError::policy(
            "unsupported_translation_profile",
            "translation provenance does not match the fixed deterministic layout profile",
        ));
    }
    if request.translation.glossary_sha256.0.is_some() {
        return Err(WorkerError::invalid(
            "unsupported_glossary",
            "the deterministic layout profile does not accept a glossary",
        ));
    }
    if request.limits.max_pages == 0 || request.limits.max_pages > MAX_PAGES {
        return Err(WorkerError::resource(
            "invalid_page_limit",
            "limits.maxPages must be between 1 and 300",
        ));
    }
    if request.limits.max_output_bytes == 0
        || request.limits.max_output_bytes > MAX_OUTPUT_BYTES
    {
        return Err(WorkerError::resource(
            "invalid_output_limit",
            "limits.maxOutputBytes must be between 1 and 500 MiB",
        ));
    }
    if request.limits.deadline_seconds == 0
        || request.limits.deadline_seconds > MAX_DEADLINE_SECONDS
    {
        return Err(WorkerError::resource(
            "invalid_deadline",
            "limits.deadlineSeconds must be between 1 and 1500",
        ));
    }
    Ok(())
}

fn valid_run_id(value: &str) -> bool {
    let mut bytes = value.bytes();
    let Some(first) = bytes.next() else {
        return false;
    };
    (first.is_ascii_lowercase() || first.is_ascii_digit())
        && value.len() <= 64
        && bytes.all(|byte| {
            byte.is_ascii_lowercase() || byte.is_ascii_digit() || byte == b'-'
        })
}

fn valid_lower_sha256(value: &str) -> bool {
    value.len() == 64
        && value
            .bytes()
            .all(|byte| byte.is_ascii_digit() || matches!(byte, b'a'..=b'f'))
}

fn valid_image_digest(value: &str) -> bool {
    value
        .strip_prefix("sha256:")
        .is_some_and(valid_lower_sha256)
}

fn read_limited_file(path: &Path, limit: u64, label: &str) -> WorkerResult<Vec<u8>> {
    let metadata = fs::symlink_metadata(path).map_err(|error| {
        WorkerError::invalid("input_unavailable", format!("cannot inspect {label}: {error}"))
    })?;
    if metadata.file_type().is_symlink() || !metadata.is_file() {
        return Err(WorkerError::policy(
            "invalid_input_type",
            format!("{label} must be a regular non-symlink file"),
        ));
    }
    if metadata.len() > limit {
        return Err(WorkerError::resource(
            "input_too_large",
            format!("{label} exceeds its worker byte limit"),
        ));
    }
    let file = File::open(path)?;
    let mut bytes = Vec::with_capacity(metadata.len() as usize);
    file.take(limit.saturating_add(1)).read_to_end(&mut bytes)?;
    if bytes.len() as u64 > limit {
        return Err(WorkerError::resource(
            "input_too_large",
            format!("{label} exceeds its worker byte limit"),
        ));
    }
    Ok(bytes)
}

fn trusted_font_path() -> WorkerResult<PathBuf> {
    let configured =
        env::var("PAPERTRANS_HARUMI_FONT").unwrap_or_else(|_| DEFAULT_FONT_PATH.to_owned());
    let path = PathBuf::from(configured);
    if !path.is_absolute() {
        return Err(WorkerError::policy(
            "untrusted_font_path",
            "PAPERTRANS_HARUMI_FONT must be an absolute path",
        ));
    }
    Ok(path)
}

fn verify_expected_font_hash(actual: &str) -> WorkerResult<()> {
    let expected = env::var("PAPERTRANS_HARUMI_FONT_SHA256").map_err(|_| {
        WorkerError::policy(
            "missing_font_hash_configuration",
            "PAPERTRANS_HARUMI_FONT_SHA256 is required",
        )
    })?;
    if !valid_lower_sha256(&expected) {
        return Err(WorkerError::policy(
            "invalid_font_hash_configuration",
            "PAPERTRANS_HARUMI_FONT_SHA256 must be 64 lowercase hexadecimal characters",
        ));
    }
    if expected != actual {
        return Err(WorkerError::policy(
            "font_hash_mismatch",
            "trusted font does not match PAPERTRANS_HARUMI_FONT_SHA256",
        ));
    }
    Ok(())
}

fn prepare_output_dir(path: &Path) -> WorkerResult<()> {
    match fs::symlink_metadata(path) {
        Ok(metadata) => {
            if metadata.file_type().is_symlink() || !metadata.is_dir() {
                return Err(WorkerError::policy(
                    "invalid_output_directory",
                    "output must be a non-symlink directory",
                ));
            }
            if fs::read_dir(path)?.next().is_some() {
                return Err(WorkerError::policy(
                    "output_not_empty",
                    "output must be a fresh empty staging directory",
                ));
            }
        }
        Err(error) if error.kind() == io::ErrorKind::NotFound => fs::create_dir_all(path)?,
        Err(error) => return Err(error.into()),
    }
    fs::create_dir(path.join("artifacts"))?;
    Ok(())
}

fn write_new_atomic(path: &Path, bytes: &[u8]) -> WorkerResult<()> {
    let file_name = path
        .file_name()
        .and_then(|name| name.to_str())
        .ok_or_else(|| WorkerError::internal("invalid_output_path", "output has no file name"))?;
    let temporary = path.with_file_name(format!(".{file_name}.tmp-{}", std::process::id()));
    let mut file = OpenOptions::new()
        .write(true)
        .create_new(true)
        .open(&temporary)?;
    file.write_all(bytes)?;
    file.sync_all()?;
    if fs::symlink_metadata(path).is_ok() {
        return Err(WorkerError::policy(
            "output_already_exists",
            "refusing to overwrite an output artifact",
        ));
    }
    fs::rename(&temporary, path)?;
    Ok(())
}

fn summarize_quality(output: &TranslateOutput) -> (Vec<PageQuality>, QualityTotals) {
    let pages: Vec<PageQuality> = output
        .quality
        .pages
        .iter()
        .map(|page| PageQuality {
            page_number: page.page_num,
            overflow_count: page.summary.overflow_count,
            collision_count: page.summary.collision_count,
            shrunk_count: page.summary.shrunk_count,
            worst_overlap_area: page.summary.worst_overlap_area,
            issue_count: page.issues.len(),
            unresolved_issue_count: page.issues.iter().filter(|issue| !issue.resolved).count(),
        })
        .collect();
    let totals = pages.iter().fold(QualityTotals::default(), |mut totals, page| {
        totals.pages += 1;
        totals.overflow_count += page.overflow_count;
        totals.collision_count += page.collision_count;
        totals.shrunk_count += page.shrunk_count;
        totals.issue_count += page.issue_count;
        totals.unresolved_issue_count += page.unresolved_issue_count;
        totals.worst_overlap_area = totals.worst_overlap_area.max(page.worst_overlap_area);
        totals
    });
    (pages, totals)
}

fn sha256_hex(bytes: &[u8]) -> String {
    let digest = Sha256::digest(bytes);
    let mut output = String::with_capacity(digest.len() * 2);
    for byte in digest {
        use fmt::Write as _;
        let _ = write!(output, "{byte:02x}");
    }
    output
}

fn quality_result_name(result: &QualityResult) -> &'static str {
    match result {
        QualityResult::Pass => "pass",
        QualityResult::Warn(_) => "warn",
        QualityResult::Fail(_) => "fail",
        _ => "unknown",
    }
}

fn translation_mode_name(mode: &TranslationMode) -> &'static str {
    match mode {
        TranslationMode::Overlay => "overlay",
        TranslationMode::NewDocument => "new_document",
        TranslationMode::InPlace => "in_place",
        TranslationMode::Auto => "auto",
        TranslationMode::Bilingual => "bilingual",
    }
}

fn rfc3339_now() -> String {
    Utc::now().to_rfc3339_opts(SecondsFormat::Millis, true)
}

fn sanitize_message(message: &str) -> String {
    let mut sanitized = String::with_capacity(message.len().min(500));
    for character in message.chars() {
        if sanitized.len() >= 500 {
            break;
        }
        if character.is_control() {
            sanitized.push(' ');
        } else {
            sanitized.push(character);
        }
    }
    sanitized
}

#[cfg(test)]
mod tests {
    use super::*;

    #[test]
    fn layout_text_is_deterministic_and_marked_as_placeholder() {
        let first = deterministic_layout_text("A source paragraph for testing.");
        let second = deterministic_layout_text("A source paragraph for testing.");
        assert_eq!(first, second);
        assert!(first.starts_with("仮訳"));
        assert!(!first.is_empty());
    }

    #[test]
    fn short_layout_text_is_never_empty() {
        assert_eq!(deterministic_layout_text("x").chars().count(), 2);
        assert_eq!(deterministic_layout_text("").chars().count(), 2);
    }

    #[test]
    fn run_id_contract_is_exact() {
        assert!(valid_run_id("pdf-harumi-01"));
        assert!(!valid_run_id("PDF-HARUMI-01"));
        assert!(!valid_run_id("-pdf-harumi-01"));
        assert!(!valid_run_id("pdf_harumi_01"));
    }

    #[test]
    fn batch_translation_preserves_page_and_block_cardinality_and_ids() {
        let raw = r#"{"pages":[{"page":3,"blocks":[{"id":7,"type":"paragraph","text":"Hello"},{"id":9,"type":"h2","text":"Heading"}]}]}"#;
        let (translated, stats) = translate_batch_json(raw).expect("batch should translate");
        let value: Value = serde_json::from_str(&translated).expect("output should be JSON");
        let blocks = value["pages"][0]["blocks"]
            .as_array()
            .expect("blocks should be an array");
        assert_eq!(value["pages"].as_array().map(Vec::len), Some(1));
        assert_eq!(blocks.len(), 2);
        assert_eq!(blocks[0]["id"], 7);
        assert_eq!(blocks[1]["id"], 9);
        assert!(blocks[0]["text"].as_str().is_some_and(|text| text.starts_with("仮訳")));
        assert!(value["pages"][0].get("page").is_none());
        assert!(blocks[0].get("type").is_none());
        assert_eq!(stats.segments, 2);
        assert_eq!(stats.source_characters, 12);
    }
}

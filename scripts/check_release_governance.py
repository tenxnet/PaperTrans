#!/usr/bin/env python3
"""Read-only validation of GitHub release-branch rules and exact-SHA CI."""

from __future__ import annotations

import argparse
import fnmatch
import json
import re
import subprocess
import sys
from pathlib import Path
from typing import Any, Iterable


REPOSITORY_RE = re.compile(r"[A-Za-z0-9_.-]+/[A-Za-z0-9_.-]+")
FULL_COMMIT_RE = re.compile(r"[0-9a-fA-F]{40}")


class GovernanceError(RuntimeError):
    """GitHub's release governance does not match the checked-in policy."""


def _load_object(path: Path) -> dict[str, Any]:
    value = json.loads(path.read_text(encoding="utf-8"))
    if not isinstance(value, dict):
        raise GovernanceError(f"{path} must contain one JSON object")
    return value


def _rule_map(ruleset: dict[str, Any]) -> dict[str, list[dict[str, Any]]]:
    result: dict[str, list[dict[str, Any]]] = {}
    rules = ruleset.get("rules")
    if not isinstance(rules, list):
        return result
    for rule in rules:
        if isinstance(rule, dict) and isinstance(rule.get("type"), str):
            result.setdefault(rule["type"], []).append(rule)
    return result


def required_checks(policy: dict[str, Any]) -> set[str]:
    checks: set[str] = set()
    for rule in _rule_map(policy).get("required_status_checks", []):
        parameters = rule.get("parameters", {})
        for check in parameters.get("required_status_checks", []):
            if isinstance(check, dict) and isinstance(check.get("context"), str):
                checks.add(check["context"])
    if not checks:
        raise GovernanceError("ruleset policy has no required status-check contexts")
    return checks


def required_check_sources(policy: dict[str, Any]) -> dict[str, int | None]:
    sources: dict[str, int | None] = {}
    for rule in _rule_map(policy).get("required_status_checks", []):
        parameters = rule.get("parameters", {})
        for check in parameters.get("required_status_checks", []):
            if isinstance(check, dict) and isinstance(check.get("context"), str):
                integration_id = check.get("integration_id")
                sources[check["context"]] = (
                    integration_id if type(integration_id) is int else None
                )
    return sources


def required_approval_count(policy: dict[str, Any]) -> int:
    counts = [
        rule.get("parameters", {}).get("required_approving_review_count", 0)
        for rule in _rule_map(policy).get("pull_request", [])
    ]
    valid_counts = [value for value in counts if type(value) is int and value >= 0]
    return max(valid_counts, default=0)


def validate_review_capacity(
    collaborators: Iterable[dict[str, Any]], *, policy: dict[str, Any]
) -> list[str]:
    required_reviewers = required_approval_count(policy)
    if required_reviewers == 0:
        return []
    review_capable_users = {
        collaborator.get("login")
        for collaborator in collaborators
        if isinstance(collaborator, dict)
        and isinstance(collaborator.get("login"), str)
        and isinstance(collaborator.get("permissions"), dict)
        and collaborator["permissions"].get("push") is True
    }
    minimum_users = required_reviewers + 1
    if len(review_capable_users) < minimum_users:
        return [
            f"only {len(review_capable_users)} review-capable collaborator(s); "
            f"at least {minimum_users} are needed for an author plus "
            f"{required_reviewers} required reviewer(s)"
        ]
    return []


def _matches_ref(pattern: str, full_ref: str, *, default_branch: str) -> bool:
    if pattern == "~ALL":
        return True
    if pattern == "~DEFAULT_BRANCH":
        return full_ref == f"refs/heads/{default_branch}"
    return fnmatch.fnmatchcase(full_ref, pattern)


def _applies_to_branch(ruleset: dict[str, Any], branch: str) -> bool:
    if ruleset.get("target") != "branch" or ruleset.get("enforcement") != "active":
        return False
    conditions = ruleset.get("conditions")
    if not isinstance(conditions, dict):
        return False
    ref_name = conditions.get("ref_name")
    if not isinstance(ref_name, dict):
        return False
    includes = ref_name.get("include", [])
    excludes = ref_name.get("exclude", [])
    full_ref = f"refs/heads/{branch}"
    return any(
        isinstance(pattern, str)
        and _matches_ref(pattern, full_ref, default_branch=branch)
        for pattern in includes
    ) and not any(
        isinstance(pattern, str)
        and _matches_ref(pattern, full_ref, default_branch=branch)
        for pattern in excludes
    )


def validate_rulesets(
    rulesets: Iterable[dict[str, Any]],
    *,
    policy: dict[str, Any],
    branch: str,
) -> list[str]:
    """Return human-readable policy violations without changing GitHub state."""

    applicable = [value for value in rulesets if _applies_to_branch(value, branch)]
    if not applicable:
        return [f"no active branch ruleset applies to refs/heads/{branch}"]

    violations: list[str] = []
    all_rules = [
        rule
        for ruleset in applicable
        for rules in _rule_map(ruleset).values()
        for rule in rules
    ]
    rule_types = {rule["type"] for rule in all_rules}
    for required_type, message in (
        ("deletion", "branch deletion is not blocked"),
        ("non_fast_forward", "force pushes are not blocked"),
        ("pull_request", "changes are not required to use pull requests"),
        ("required_status_checks", "required status checks are not configured"),
    ):
        if required_type not in rule_types:
            violations.append(message)

    pull_request_rules = [rule for rule in all_rules if rule["type"] == "pull_request"]
    if pull_request_rules:
        actual_approvals = required_approval_count({"rules": pull_request_rules})
        policy_approvals = required_approval_count(policy)
        if actual_approvals < policy_approvals:
            violations.append(
                "required approving-review count is below policy: "
                f"{actual_approvals} < {policy_approvals}"
            )

    actual_checks: set[str] = set()
    actual_sources: dict[str, set[int | None]] = {}
    status_rules = [rule for rule in all_rules if rule["type"] == "required_status_checks"]
    for rule in status_rules:
        parameters = rule.get("parameters", {})
        if parameters.get("strict_required_status_checks_policy") is not True:
            violations.append("required status checks do not require the latest main commit")
        for check in parameters.get("required_status_checks", []):
            if isinstance(check, dict) and isinstance(check.get("context"), str):
                actual_checks.add(check["context"])
                integration_id = check.get("integration_id")
                actual_sources.setdefault(check["context"], set()).add(
                    integration_id if type(integration_id) is int else None
                )
    missing_checks = required_checks(policy) - actual_checks
    if missing_checks:
        violations.append("missing required checks: " + ", ".join(sorted(missing_checks)))

    for context, integration_id in required_check_sources(policy).items():
        if (
            integration_id is not None
            and integration_id not in actual_sources.get(context, set())
        ):
            violations.append(
                f"required check is not pinned to integration {integration_id}: {context}"
            )

    unverifiable_bypasses = [
        ruleset.get("name", ruleset.get("id", "unnamed"))
        for ruleset in applicable
        if not isinstance(ruleset.get("bypass_actors"), list)
    ]
    if unverifiable_bypasses:
        violations.append(
            "could not verify bypass actors for: "
            + ", ".join(str(value) for value in unverifiable_bypasses)
        )
    bypasses = [
        actor
        for ruleset in applicable
        for actor in (
            ruleset["bypass_actors"]
            if isinstance(ruleset.get("bypass_actors"), list)
            else []
        )
        if isinstance(actor, dict)
    ]
    if bypasses:
        violations.append("ruleset has a bypass actor")
    return violations


def validate_ci_run(
    runs: Iterable[dict[str, Any]],
    jobs_by_run: dict[int, list[dict[str, Any]]],
    *,
    expected_commit: str,
    expected_branch: str,
    policy: dict[str, Any],
) -> tuple[int | None, list[str]]:
    successful_runs = [
        run
        for run in runs
        if run.get("head_sha") == expected_commit
        and run.get("head_branch") == expected_branch
        and run.get("event") == "push"
        and run.get("status") == "completed"
        and run.get("conclusion") == "success"
        and isinstance(run.get("id"), int)
    ]
    if not successful_runs:
        return None, [f"no successful completed CI push run exists for {expected_commit}"]
    run = max(successful_runs, key=lambda value: value.get("run_number", 0))
    run_id = run["id"]
    conclusions = {
        job.get("name"): job.get("conclusion")
        for job in jobs_by_run.get(run_id, [])
        if isinstance(job, dict) and isinstance(job.get("name"), str)
    }
    violations = [
        f"required CI job did not succeed: {name} ({conclusions.get(name, 'missing')})"
        for name in sorted(required_checks(policy))
        if conclusions.get(name) != "success"
    ]
    return run_id, violations


def _gh_json(repository: str, endpoint: str) -> Any:
    target = f"repos/{repository}/{endpoint}" if endpoint else f"repos/{repository}"
    result = subprocess.run(
        ["gh", "api", target],
        check=False,
        stdout=subprocess.PIPE,
        stderr=subprocess.PIPE,
        text=True,
    )
    if result.returncode != 0:
        raise GovernanceError(result.stderr.strip() or f"GitHub API request failed: {endpoint}")
    return json.loads(result.stdout)


def fetch_rulesets(repository: str) -> list[dict[str, Any]]:
    summaries = _gh_json(
        repository, "rulesets?includes_parents=true&targets=branch&per_page=100"
    )
    if not isinstance(summaries, list):
        raise GovernanceError("GitHub returned an invalid ruleset list")
    details = []
    for summary in summaries:
        ruleset_id = summary.get("id") if isinstance(summary, dict) else None
        if not isinstance(ruleset_id, int):
            raise GovernanceError("GitHub returned a ruleset without a numeric ID")
        detail = _gh_json(repository, f"rulesets/{ruleset_id}?includes_parents=true")
        if not isinstance(detail, dict):
            raise GovernanceError(f"GitHub returned an invalid ruleset {ruleset_id}")
        details.append(detail)
    return details


def fetch_repository(repository: str) -> dict[str, Any]:
    value = _gh_json(repository, "")
    if not isinstance(value, dict):
        raise GovernanceError("GitHub returned invalid repository metadata")
    return value


def fetch_collaborators(repository: str) -> list[dict[str, Any]]:
    value = _gh_json(repository, "collaborators?affiliation=all&per_page=100")
    if not isinstance(value, list):
        raise GovernanceError("GitHub returned an invalid collaborator list")
    return value


def fetch_ci(
    repository: str, commit: str, branch: str
) -> tuple[list[dict[str, Any]], dict[int, list[dict[str, Any]]]]:
    response = _gh_json(
        repository,
        f"actions/workflows/ci.yml/runs?head_sha={commit}&branch={branch}"
        "&event=push&per_page=100",
    )
    runs = response.get("workflow_runs", []) if isinstance(response, dict) else []
    if not isinstance(runs, list):
        raise GovernanceError("GitHub returned an invalid workflow-run list")
    jobs_by_run: dict[int, list[dict[str, Any]]] = {}
    for run in runs:
        run_id = run.get("id") if isinstance(run, dict) else None
        if not isinstance(run_id, int):
            continue
        jobs = _gh_json(repository, f"actions/runs/{run_id}/jobs?per_page=100")
        values = jobs.get("jobs", []) if isinstance(jobs, dict) else []
        if not isinstance(values, list):
            raise GovernanceError(f"GitHub returned invalid jobs for run {run_id}")
        jobs_by_run[run_id] = values
    return runs, jobs_by_run


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--repository", default="tenxnet/PaperTrans")
    parser.add_argument("--branch", default="main")
    parser.add_argument("--commit", help="Also require a successful CI push run for this full SHA")
    parser.add_argument(
        "--policy",
        type=Path,
        default=Path(__file__).resolve().parents[1] / ".github" / "main-ruleset.json",
    )
    arguments = parser.parse_args(argv)

    if REPOSITORY_RE.fullmatch(arguments.repository) is None:
        parser.error("--repository must use OWNER/REPOSITORY form")
    if arguments.commit is not None and FULL_COMMIT_RE.fullmatch(arguments.commit) is None:
        parser.error("--commit must be a full 40-character Git commit SHA")

    try:
        policy = _load_object(arguments.policy)
        repository_metadata = fetch_repository(arguments.repository)
        if repository_metadata.get("default_branch") != arguments.branch:
            violations = [
                "repository default branch is "
                f"{repository_metadata.get('default_branch')!r}, expected {arguments.branch!r}"
            ]
        else:
            violations = []
        violations.extend(
            validate_review_capacity(
                fetch_collaborators(arguments.repository), policy=policy
            )
        )
        violations.extend(
            validate_rulesets(
                fetch_rulesets(arguments.repository),
                policy=policy,
                branch=arguments.branch,
            )
        )
        run_id = None
        if arguments.commit is not None:
            runs, jobs_by_run = fetch_ci(
                arguments.repository, arguments.commit.lower(), arguments.branch
            )
            run_id, ci_violations = validate_ci_run(
                runs,
                jobs_by_run,
                expected_commit=arguments.commit.lower(),
                expected_branch=arguments.branch,
                policy=policy,
            )
            violations.extend(ci_violations)
    except (GovernanceError, json.JSONDecodeError, OSError) as error:
        print(f"release governance gate failed: {error}", file=sys.stderr)
        return 1

    if violations:
        for violation in violations:
            print(f"release governance gate failed: {violation}", file=sys.stderr)
        return 1

    suffix = f"; successful CI run {run_id}" if run_id is not None else ""
    print(f"release governance verified for {arguments.repository}:{arguments.branch}{suffix}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())

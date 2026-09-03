from __future__ import annotations

import copy
import json
import sys
from pathlib import Path

import yaml

REPOSITORY_ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(REPOSITORY_ROOT))

from scripts.check_release_governance import (
    required_approval_count,
    required_checks,
    validate_ci_run,
    validate_review_capacity,
    validate_rulesets,
)


def _policy() -> dict[str, object]:
    return json.loads(
        (REPOSITORY_ROOT / ".github/main-ruleset.json").read_text(encoding="utf-8")
    )


def test_checked_in_ruleset_policy_satisfies_release_requirements() -> None:
    policy = _policy()
    assert validate_rulesets([policy], policy=policy, branch="main") == []


def test_ruleset_check_names_match_stable_ci_job_names() -> None:
    workflow = yaml.safe_load(
        (REPOSITORY_ROOT / ".github/workflows/ci.yml").read_text(encoding="utf-8")
    )
    job_names = {job["name"] for job in workflow["jobs"].values()}
    assert required_checks(_policy()) == job_names


def test_python_ci_audits_both_supported_lock_branches_and_keeps_tree_clean() -> None:
    workflow = (REPOSITORY_ROOT / ".github/workflows/ci.yml").read_text(
        encoding="utf-8"
    )

    assert "for python_version in 3.10 3.11 3.12" in workflow
    assert 'uvx --python "$python_version" --from pip-audit==2.10.1' in workflow
    assert 'uv build --out-dir "$RUNNER_TEMP/papertrans-dist"' in workflow
    assert "fetch-depth: 0" in workflow
    assert "--require-ancestor-of refs/remotes/origin/main" in workflow


def test_ruleset_validator_reports_missing_review_and_check() -> None:
    policy = _policy()
    actual = copy.deepcopy(policy)
    actual["rules"] = [
        rule
        for rule in actual["rules"]
        if rule["type"] not in {"pull_request", "required_status_checks"}
    ]

    violations = validate_rulesets([actual], policy=policy, branch="main")
    assert "changes are not required to use pull requests" in violations
    assert "required status checks are not configured" in violations
    assert any(value.startswith("missing required checks:") for value in violations)


def test_exact_sha_ci_gate_requires_every_named_job() -> None:
    policy = _policy()
    commit = "a" * 40
    run = {
        "id": 42,
        "run_number": 10,
        "head_sha": commit,
        "head_branch": "main",
        "event": "push",
        "status": "completed",
        "conclusion": "success",
    }
    jobs = [
        {"name": name, "conclusion": "success"}
        for name in sorted(required_checks(policy))
    ]
    run_id, violations = validate_ci_run(
        [run],
        {42: jobs},
        expected_commit=commit,
        expected_branch="main",
        policy=policy,
    )
    assert run_id == 42
    assert violations == []

    jobs.pop()
    _, violations = validate_ci_run(
        [run],
        {42: jobs},
        expected_commit=commit,
        expected_branch="main",
        policy=policy,
    )
    assert len(violations) == 1
    assert "missing" in violations[0]


def test_solo_policy_requires_pr_without_an_impossible_self_review() -> None:
    policy = _policy()
    assert required_approval_count(policy) == 0
    owner = {
        "login": "owner",
        "permissions": {"admin": True, "push": True},
    }
    assert validate_review_capacity([owner], policy=policy) == []


def test_review_capacity_is_enforced_when_policy_requires_approval() -> None:
    policy = copy.deepcopy(_policy())
    pull_request = next(rule for rule in policy["rules"] if rule["type"] == "pull_request")
    pull_request["parameters"]["required_approving_review_count"] = 1
    owner = {
        "login": "owner",
        "permissions": {"admin": True, "push": True},
    }
    reviewer = {
        "login": "reviewer",
        "permissions": {"admin": False, "push": True},
    }

    violations = validate_review_capacity([owner], policy=policy)
    assert len(violations) == 1
    assert "at least 2" in violations[0]
    assert validate_review_capacity([owner, reviewer], policy=policy) == []

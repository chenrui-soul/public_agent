from pathlib import Path

import pytest
from pydantic import ValidationError

from public_agent.domains.loader import DomainPackageLoader
from public_agent.domains.models import DomainPackageEvaluationResult


def test_load_example_domain_package() -> None:
    root = Path(__file__).parents[1] / "examples" / "domain_packs" / "calculator"
    package = DomainPackageLoader().load(root)

    assert package.id == "calculator_assistant"
    assert package.version == "0.1.0"
    assert package.allowed_tools == ("add_numbers",)
    assert "calculation specialist" in package.instructions

    prepared = DomainPackageLoader().build(root)

    assert len(prepared.assets) == 2
    assert {asset.key for asset in prepared.assets} == {"instructions", "domain_policies"}
    assert len(prepared.content_hash) == 64


def test_reject_instruction_path_outside_package(tmp_path: Path) -> None:
    outside = tmp_path / "outside.md"
    outside.write_text("untrusted", encoding="utf-8")
    package = tmp_path / "package"
    package.mkdir()
    (package / "manifest.yaml").write_text(
        "\n".join(
            [
                "id: unsafe_domain",
                "name: Unsafe",
                "version: 0.1.0",
                "instructions_file: ../outside.md",
                "memory_namespace: unsafe",
            ]
        ),
        encoding="utf-8",
    )

    with pytest.raises(ValueError, match="inside the domain package"):
        DomainPackageLoader().load(package)


def test_load_optional_knowledge_retrieval_configuration(tmp_path: Path) -> None:
    (tmp_path / "manifest.yaml").write_text(
        "\n".join(
            [
                "id: support_agent",
                "name: Support Agent",
                "version: 0.1.0",
                "instructions: Answer support questions.",
                "memory_namespace: support-memory",
                "knowledge_namespace: support-manuals",
                "knowledge_top_k: 7",
            ]
        ),
        encoding="utf-8",
    )

    package = DomainPackageLoader().load(tmp_path)
    spec = package.to_agent_spec()

    assert spec.knowledge_namespace == "support-manuals"
    assert spec.knowledge_top_k == 7
    assert spec.metadata["domain_id"] == "support_agent"


def test_build_hash_is_location_and_line_ending_independent(tmp_path: Path) -> None:
    first = tmp_path / "first"
    second = tmp_path / "second"
    for root, newline in ((first, "\n"), (second, "\r\n")):
        (root / "skills").mkdir(parents=True)
        (root / "policies").mkdir()
        (root / "instructions.md").write_bytes(
            f"Use the approved workflow.{newline}".encode()
        )
        (root / "skills" / "review.yaml").write_bytes(
            f"steps:{newline}  - validate{newline}".encode()
        )
        (root / "policies" / "rules.yaml").write_bytes(
            f"require_review: true{newline}".encode()
        )
        skill_lines = [
            "  - asset_type: skill",
            "    key: review",
            "    path: skills/review.yaml",
            "    media_type: application/yaml",
        ]
        policy_lines = [
            "  - asset_type: policy",
            "    key: review_rules",
            "    path: policies/rules.yaml",
            "    media_type: application/yaml",
        ]
        asset_lines = (
            [*skill_lines, *policy_lines]
            if root == first
            else [*policy_lines, *skill_lines]
        )
        (root / "manifest.yaml").write_text(
            "\n".join(
                [
                    "id: review_agent",
                    "name: Review Agent",
                    "version: 1.2.3",
                    "instructions_file: instructions.md",
                    "memory_namespace: review",
                    "assets:",
                    *asset_lines,
                ]
            ),
            encoding="utf-8",
        )

    loader = DomainPackageLoader()
    first_build = loader.build(first)
    second_build = loader.build(second)

    assert first_build.content_hash == second_build.content_hash
    assert [asset.content_hash for asset in first_build.assets] == [
        asset.content_hash for asset in second_build.assets
    ]

    (second / "skills" / "review.yaml").write_text(
        "steps:\n  - validate\n  - approve\n",
        encoding="utf-8",
    )

    assert loader.build(second).content_hash != first_build.content_hash


@pytest.mark.parametrize(
    ("asset_lines", "message"),
    [
        (
            [
                "  - asset_type: skill",
                "    key: review",
                "    path: skills/review.yaml",
                "  - asset_type: skill",
                "    key: review",
                "    path: skills/other.yaml",
            ],
            "type and key must be unique",
        ),
        (
            [
                "  - asset_type: skill",
                "    key: review",
                "    path: skills/review.yaml",
                "  - asset_type: workflow",
                "    key: review_flow",
                "    path: skills/review.yaml",
            ],
            "paths must be unique",
        ),
    ],
)
def test_reject_duplicate_asset_identity_or_path(
    tmp_path: Path,
    asset_lines: list[str],
    message: str,
) -> None:
    (tmp_path / "skills").mkdir()
    (tmp_path / "skills" / "review.yaml").write_text("review", encoding="utf-8")
    (tmp_path / "skills" / "other.yaml").write_text("other", encoding="utf-8")
    (tmp_path / "manifest.yaml").write_text(
        "\n".join(
            [
                "id: review_agent",
                "name: Review Agent",
                "version: 1.0.0",
                "instructions: Review safely.",
                "memory_namespace: review",
                "assets:",
                *asset_lines,
            ]
        ),
        encoding="utf-8",
    )

    with pytest.raises(ValidationError, match=message):
        DomainPackageLoader().build(tmp_path)


@pytest.mark.parametrize(
    "asset_path",
    ["../outside.md", "C:\\outside.md", "/outside.md"],
)
def test_reject_declared_asset_path_escape(tmp_path: Path, asset_path: str) -> None:
    (tmp_path / "manifest.yaml").write_text(
        "\n".join(
            [
                "id: review_agent",
                "name: Review Agent",
                "version: 1.0.0",
                "instructions: Review safely.",
                "memory_namespace: review",
                "assets:",
                "  - asset_type: skill",
                "    key: review",
                f"    path: '{asset_path}'",
            ]
        ),
        encoding="utf-8",
    )

    with pytest.raises(ValueError, match="inside the domain package"):
        DomainPackageLoader().build(tmp_path)


def test_reject_missing_directory_non_utf8_and_oversized_assets(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    asset = tmp_path / "asset.yaml"
    manifest = tmp_path / "manifest.yaml"
    manifest.write_text(
        "\n".join(
            [
                "id: review_agent",
                "name: Review Agent",
                "version: 1.0.0",
                "instructions: Review safely.",
                "memory_namespace: review",
                "assets:",
                "  - asset_type: skill",
                "    key: review",
                "    path: asset.yaml",
            ]
        ),
        encoding="utf-8",
    )
    loader = DomainPackageLoader()

    with pytest.raises(FileNotFoundError):
        loader.build(tmp_path)

    asset.mkdir()
    with pytest.raises(ValueError, match="regular file"):
        loader.build(tmp_path)

    asset.rmdir()
    asset.write_bytes(b"\xff\xfe")
    with pytest.raises(ValueError, match="valid UTF-8"):
        loader.build(tmp_path)

    asset.write_text("large", encoding="utf-8")
    monkeypatch.setattr(DomainPackageLoader, "MAX_ASSET_BYTES", 4)
    with pytest.raises(ValueError, match="size limit"):
        loader.build(tmp_path)


def test_reject_total_asset_capacity_and_invalid_semver(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    (tmp_path / "asset.yaml").write_text("x" * 200, encoding="utf-8")
    (tmp_path / "manifest.yaml").write_text(
        "\n".join(
            [
                "id: review_agent",
                "name: Review Agent",
                "version: '1.0'",
                "instructions: Review safely.",
                "memory_namespace: review",
                "assets:",
                "  - asset_type: skill",
                "    key: review",
                "    path: asset.yaml",
            ]
        ),
        encoding="utf-8",
    )

    with pytest.raises(ValidationError, match="semantic versioning"):
        DomainPackageLoader().build(tmp_path)

    manifest = (tmp_path / "manifest.yaml").read_text(encoding="utf-8")
    (tmp_path / "manifest.yaml").write_text(
        manifest.replace("version: '1.0'", "version: 1.0.0"),
        encoding="utf-8",
    )
    monkeypatch.setattr(DomainPackageLoader, "MAX_TOTAL_ASSET_BYTES", 150)
    with pytest.raises(ValueError, match="total size limit"):
        DomainPackageLoader().build(tmp_path)


def test_evaluation_report_hash_is_derived_and_rejects_mismatch() -> None:
    evaluation = DomainPackageEvaluationResult(
        suite="review_v1",
        dataset_version="1",
        passed=True,
        score=1,
        summary="passed",
        metrics={"cases": 3},
    )

    assert len(evaluation.report_hash) == 64
    with pytest.raises(ValidationError, match="does not match"):
        DomainPackageEvaluationResult(
            suite="review_v1",
            dataset_version="1",
            passed=True,
            score=1,
            summary="passed",
            metrics={"cases": 3},
            report_hash="0" * 64,
        )


def test_reject_resolved_asset_that_escapes_package(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    outside = tmp_path / "outside.yaml"
    outside.write_text("outside", encoding="utf-8")
    package = tmp_path / "package"
    package.mkdir()
    (package / "manifest.yaml").write_text(
        "\n".join(
            [
                "id: review_agent",
                "name: Review Agent",
                "version: 1.0.0",
                "instructions: Review safely.",
                "memory_namespace: review",
                "assets:",
                "  - asset_type: skill",
                "    key: review",
                "    path: linked.yaml",
            ]
        ),
        encoding="utf-8",
    )
    original_resolve = Path.resolve

    def resolve_with_escape(path: Path, *, strict: bool = False) -> Path:
        if path.name == "linked.yaml":
            return original_resolve(outside, strict=True)
        return original_resolve(path, strict=strict)

    monkeypatch.setattr(Path, "resolve", resolve_with_escape)

    with pytest.raises(ValueError, match="inside the domain package"):
        DomainPackageLoader().build(package)

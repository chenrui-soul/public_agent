from __future__ import annotations

import hashlib
import json
import unicodedata
from pathlib import Path, PurePosixPath, PureWindowsPath
from typing import Any

import yaml

from public_agent.domains.models import (
    DomainAssetType,
    DomainPackage,
    PreparedDomainAsset,
    PreparedDomainPackage,
)


class DomainPackageLoader:
    MANIFEST_NAME = "manifest.yaml"
    MAX_MANIFEST_BYTES = 256 * 1024
    MAX_ASSET_BYTES = 1024 * 1024
    MAX_TOTAL_ASSET_BYTES = 8 * 1024 * 1024

    def load(self, package_path: str | Path) -> DomainPackage:
        return self.build(package_path).package

    def build(self, package_path: str | Path) -> PreparedDomainPackage:
        package, root, instructions_path = self._load_package(package_path)
        assets: list[PreparedDomainAsset] = []
        seen_paths: set[str] = set()

        instruction_relative_path = "@inline/instructions"
        if instructions_path is not None:
            instruction_relative_path = instructions_path.relative_to(root).as_posix()
            seen_paths.add(instruction_relative_path.casefold())
        assets.append(
            self._prepared_asset(
                asset_type=DomainAssetType.INSTRUCTIONS,
                key="instructions",
                relative_path=instruction_relative_path,
                media_type="text/markdown",
                content=package.instructions,
            )
        )
        policy_content = self._canonical_json(package.policies.model_dump(mode="json"))
        assets.append(
            self._prepared_asset(
                asset_type=DomainAssetType.POLICY,
                key="domain_policies",
                relative_path="@inline/policies",
                media_type="application/json",
                content=policy_content,
            )
        )
        total_size = sum(asset.size_bytes for asset in assets)
        if total_size > self.MAX_TOTAL_ASSET_BYTES:
            raise ValueError("domain package assets exceed the total size limit")

        for declaration in sorted(
            package.assets,
            key=lambda item: (item.asset_type.value, item.key, item.path),
        ):
            asset_path, relative_path = self._resolve_asset(root, declaration.path)
            path_key = relative_path.casefold()
            if path_key in seen_paths:
                raise ValueError("domain asset paths must be unique")
            seen_paths.add(path_key)
            content = self._read_text(asset_path, limit=self.MAX_ASSET_BYTES)
            asset = self._prepared_asset(
                asset_type=declaration.asset_type,
                key=declaration.key,
                relative_path=relative_path,
                media_type=declaration.media_type,
                content=content,
            )
            total_size += asset.size_bytes
            if total_size > self.MAX_TOTAL_ASSET_BYTES:
                raise ValueError("domain package assets exceed the total size limit")
            assets.append(asset)
        manifest = package.model_dump(mode="json")
        manifest["assets"] = sorted(
            manifest["assets"],
            key=lambda item: (item["asset_type"], item["key"], item["path"]),
        )
        canonical = self._canonical_json(
            {
                "manifest": manifest,
                "assets": [
                    {
                        "asset_type": asset.asset_type.value,
                        "key": asset.key,
                        "relative_path": asset.relative_path,
                        "media_type": asset.media_type,
                        "content_hash": asset.content_hash,
                        "size_bytes": asset.size_bytes,
                    }
                    for asset in assets
                ],
            }
        )
        return PreparedDomainPackage(
            package=package,
            content_hash=self._sha256(canonical),
            manifest=manifest,
            assets=tuple(assets),
            total_size_bytes=total_size,
        )

    def _load_package(
        self,
        package_path: str | Path,
    ) -> tuple[DomainPackage, Path, Path | None]:
        root = Path(package_path).resolve(strict=True)
        if not root.is_dir():
            raise ValueError("Domain package path must be a directory")
        manifest_path, _ = self._resolve_asset(root, self.MANIFEST_NAME)
        raw = yaml.safe_load(self._read_text(manifest_path, limit=self.MAX_MANIFEST_BYTES))
        if not isinstance(raw, dict):
            raise ValueError("Domain manifest must contain a YAML object")

        data: dict[str, Any] = dict(raw)
        instructions_file = data.pop("instructions_file", None)
        instructions_path: Path | None = None
        if instructions_file is not None:
            if data.get("instructions"):
                raise ValueError("use either instructions or instructions_file, not both")
            instructions_path, _ = self._resolve_asset(root, str(instructions_file))
            data["instructions"] = self._read_text(
                instructions_path,
                limit=self.MAX_ASSET_BYTES,
            ).strip()
        if not data.get("instructions"):
            raise ValueError("Domain package requires instructions or instructions_file")
        data["instructions"] = self._normalize_text(str(data["instructions"])).strip()

        return DomainPackage.model_validate(data), root, instructions_path

    @staticmethod
    def _resolve_asset(root: Path, raw_path: str) -> tuple[Path, str]:
        normalized = raw_path.replace("\\", "/")
        posix_path = PurePosixPath(normalized)
        windows_path = PureWindowsPath(raw_path)
        if (
            not normalized
            or posix_path.is_absolute()
            or windows_path.is_absolute()
            or windows_path.drive
            or any(part in {"", ".", ".."} for part in posix_path.parts)
        ):
            raise ValueError("domain asset path must stay inside the domain package")
        candidate = (root / Path(*posix_path.parts)).resolve(strict=True)
        if not candidate.is_relative_to(root):
            raise ValueError("domain asset must stay inside the domain package")
        if not candidate.is_file():
            raise ValueError("domain asset path must reference a regular file")
        return candidate, posix_path.as_posix()

    @classmethod
    def _read_text(cls, path: Path, *, limit: int) -> str:
        size = path.stat().st_size
        if size > limit:
            raise ValueError(f"domain asset exceeds the {limit}-byte size limit")
        raw = path.read_bytes()
        if len(raw) > limit:
            raise ValueError(f"domain asset exceeds the {limit}-byte size limit")
        try:
            return cls._normalize_text(raw.decode("utf-8"))
        except UnicodeDecodeError as exc:
            raise ValueError("domain assets must be valid UTF-8 text") from exc

    @classmethod
    def _prepared_asset(
        cls,
        *,
        asset_type: DomainAssetType,
        key: str,
        relative_path: str,
        media_type: str,
        content: str,
    ) -> PreparedDomainAsset:
        normalized = cls._normalize_text(content)
        return PreparedDomainAsset(
            asset_type=asset_type,
            key=key,
            relative_path=relative_path,
            media_type=media_type,
            content=normalized,
            content_hash=cls._sha256(normalized),
            size_bytes=len(normalized.encode("utf-8")),
        )

    @staticmethod
    def _normalize_text(value: str) -> str:
        return unicodedata.normalize("NFC", value.replace("\r\n", "\n").replace("\r", "\n"))

    @staticmethod
    def _canonical_json(value: Any) -> str:
        return json.dumps(
            value,
            ensure_ascii=False,
            separators=(",", ":"),
            sort_keys=True,
        )

    @staticmethod
    def _sha256(value: str) -> str:
        return hashlib.sha256(value.encode("utf-8")).hexdigest()

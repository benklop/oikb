"""One-shot file additions to a Knowledge Base (no diff, no delete propagation).

Unlike ``sync``, ``add`` never removes anything: it uploads the given files
and links them to the KB. Files already present in the KB (matched by
filename + SHA-256) are skipped, so re-runs are idempotent.

Supports two upload modes:
  - normal:    the file bytes are uploaded to Open WebUI storage
  - reference: a 1-byte sentinel is uploaded and the real bytes are pointed
               at via ``external_ref.path`` metadata (requires an Open WebUI
               with ENABLE_REFERENCE_FILES; the bytes stay in the external
               archive and are served on demand)
"""

from __future__ import annotations

import hashlib
import os
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any

import httpx

from oikb.client import OikbClient

_SENTINEL = b"\n"


@dataclass
class AddResult:
    added: int = 0
    skipped: int = 0
    errors: list[str] = field(default_factory=list)

    @property
    def ok(self) -> bool:
        return not self.errors

    def summary(self) -> str:
        parts = []
        if self.added:
            parts.append(f"{self.added} added")
        if self.skipped:
            parts.append(f"{self.skipped} skipped (already in KB)")
        if self.errors:
            parts.append(f"{len(self.errors)} error(s)")
        return ", ".join(parts) if parts else "nothing to do"


def sha256_file(path: str | Path) -> str:
    h = hashlib.sha256()
    with open(path, "rb") as f:
        while chunk := f.read(65_536):
            h.update(chunk)
    return h.hexdigest()


def resolve_kb(client: OikbClient, kb_id: str | None, kb_name: str | None) -> dict[str, Any]:
    """Resolve the target KB by id, or find-or-create it by name."""
    if kb_id:
        return client.get_kb(kb_id)
    if not kb_name:
        raise ValueError("either --kb-id or --kb-name is required")
    for kb in client.list_kbs():
        if kb.get("name") == kb_name:
            return kb
    return client.create_kb(kb_name)


def ensure_directory(
    client: OikbClient, kb_id: str, rel_dir: str | None
) -> str | None:
    """Ensure each segment of rel_dir exists in the KB; return the leaf id."""
    if not rel_dir:
        return None
    dirs = client.list_directories(kb_id)
    by_name: dict[str, dict[str, Any]] = {}
    for d in dirs:
        by_name.setdefault(d["name"], d)

    parent_id: str | None = None
    leaf_id: str | None = None
    for segment in [s for s in rel_dir.split("/") if s]:
        existing = by_name.get(segment)
        if existing and existing.get("parent_id") == parent_id:
            parent_id = existing["id"]
            leaf_id = parent_id
            continue
        created = client.create_directory(kb_id, segment, parent_id)
        parent_id = created["id"]
        leaf_id = parent_id
    return leaf_id


def _existing_kb_hashes(client: OikbClient, kb_id: str) -> dict[str, str]:
    """Map filename -> stored file_hash for files already in the KB."""
    out: dict[str, str] = {}
    for f in client.list_kb_files(kb_id):
        meta = f.get("meta") or {}
        name = meta.get("name") or f.get("filename")
        if name and meta.get("file_hash"):
            out[name] = meta["file_hash"]
    return out


def add_files(
    client: OikbClient,
    kb: dict[str, Any],
    paths: list[str | Path],
    *,
    rel_dir: str | None = None,
    index: bool = True,
    reference: bool = False,
    dry_run: bool = False,
    verbose: bool = False,
) -> AddResult:
    """Upload and link each path to the KB. See module docstring."""
    kb_id = kb["id"]
    result = AddResult()

    if dry_run:
        for p in paths:
            print(f"  + {p} → KB {kb.get('name', kb_id)}" + (" [reference]" if reference else ""))
        return result

    directory_id = ensure_directory(client, kb_id, rel_dir)
    existing = _existing_kb_hashes(client, kb_id)

    for p in paths:
        path = Path(p)
        if not path.is_file():
            result.errors.append(f"{p}: not a file")
            continue
        file_hash = sha256_file(path)
        if existing.get(path.name) == file_hash:
            result.skipped += 1
            if verbose:
                print(f"  = {path.name} (unchanged)")
            continue
        try:
            if reference:
                metadata = {
                    "file_hash": file_hash,
                    "external_ref": {"path": str(path.resolve())},
                    "source_path": str(path.resolve()),
                }
                resp = client.upload_file(
                    file_content=_SENTINEL,
                    filename=path.name,
                    kb_id=kb_id,
                    file_hash=file_hash,
                    directory_id=directory_id,
                    process=index,
                    extra_metadata=metadata,
                )
            else:
                content = path.read_bytes()
                resp = client.upload_file(
                    file_content=content,
                    filename=path.name,
                    kb_id=kb_id,
                    file_hash=file_hash,
                    directory_id=directory_id,
                    process=index,
                    extra_metadata={"source_path": str(path.resolve())},
                )
            file_id = resp.get("id")
            if not file_id:
                raise RuntimeError(f"upload response missing id: {resp}")
            client.add_file_to_knowledge(kb_id, file_id, directory_id=directory_id, index=index)
            result.added += 1
            if verbose:
                print(f"  + {path.name}")
        except (httpx.HTTPStatusError, RuntimeError, OSError) as e:
            detail = e
            if isinstance(e, httpx.HTTPStatusError):
                try:
                    payload = e.response.json()
                    if isinstance(payload, dict) and payload.get("detail"):
                        detail = payload["detail"]
                except ValueError:
                    pass
            result.errors.append(f"{path.name}: {detail}")

    return result

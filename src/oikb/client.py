"""HTTP client wrapping the Open WebUI Knowledge Base sync API."""

from __future__ import annotations

import json
from typing import Any

import httpx


class OikbClient:
    """Stateless HTTP client for the Open WebUI KB API.

    All methods are synchronous — httpx handles connection pooling internally.
    """

    def __init__(self, base_url: str, token: str, timeout: float = 120.0):
        self._base_url = base_url.rstrip("/")
        self._http = httpx.Client(
            base_url=f"{self._base_url}/api/v1",
            headers={"Authorization": f"Bearer {token}"},
            timeout=timeout,
        )

    def __enter__(self) -> OikbClient:
        return self

    def __exit__(self, *exc: object) -> None:
        self.close()

    def close(self) -> None:
        self._http.close()

    # ── Sync API ────────────────────────────────────────────────

    def sync_diff(
        self,
        kb_id: str,
        manifest: list[dict[str, Any]],
    ) -> dict[str, Any]:
        """POST /knowledge/{id}/sync/diff — compute diff from manifest."""
        resp = self._http.post(
            f"/knowledge/{kb_id}/sync/diff",
            json={"manifest": manifest},
        )
        resp.raise_for_status()
        return resp.json()

    def sync_cleanup(
        self,
        kb_id: str,
        file_ids: list[str],
        dir_ids: list[str] | None = None,
    ) -> dict[str, Any]:
        """POST /knowledge/{id}/sync/cleanup — remove stale files and dirs."""
        payload: dict[str, Any] = {"file_ids": file_ids}
        if dir_ids:
            payload["dir_ids"] = dir_ids
        resp = self._http.post(
            f"/knowledge/{kb_id}/sync/cleanup",
            json=payload,
        )
        resp.raise_for_status()
        return resp.json()

    # ── File upload ─────────────────────────────────────────────

    def upload_file(
        self,
        file_content: bytes,
        filename: str,
        kb_id: str,
        file_hash: str,
        directory_id: str | None = None,
        process: bool = True,
        extra_metadata: dict[str, Any] | None = None,
        link: bool = True,
    ) -> dict[str, Any]:
        """POST /files/ — upload a single file to the KB.

        ``process=False`` registers the file without extracting/indexing it
        (browse-only companions). ``extra_metadata`` is merged into the
        upload metadata (e.g. ``external_ref`` for reference files,
        ``source_path`` for provenance).

        ``link=False`` omits ``knowledge_id`` from the upload metadata so the
        server does NOT auto-link/auto-embed the file; the caller then links
        it explicitly via :meth:`add_file_to_knowledge`, which is the single
        embedding pass (avoids the double-embed of auto-link + explicit add).
        """

        metadata: dict[str, Any] = {
            "file_hash": file_hash,
        }
        if link:
            metadata["knowledge_id"] = kb_id
        if directory_id:
            metadata["directory_id"] = directory_id
        if extra_metadata:
            metadata.update(extra_metadata)

        # `process` is a QUERY parameter on POST /files/ (not a form field).
        params = {} if process else {"process": "false"}
        resp = self._http.post(
            "/files/",
            params=params,
            files={"file": (filename, file_content)},
            data={"metadata": json.dumps(metadata)},
        )
        resp.raise_for_status()
        return resp.json()

    def add_file_to_knowledge(
        self,
        kb_id: str,
        file_id: str,
        directory_id: str | None = None,
        index: bool = True,
    ) -> dict[str, Any]:
        """POST /knowledge/{id}/file/add — link an existing file to the KB.

        ``index=False`` links without embedding (browse-only companion);
        requires an Open WebUI supporting the extended form.
        """
        payload: dict[str, Any] = {"file_id": file_id, "index": index}
        if directory_id:
            payload["directory_id"] = directory_id
        resp = self._http.post(f"/knowledge/{kb_id}/file/add", json=payload)
        resp.raise_for_status()
        return resp.json()

    # ── Directory management ────────────────────────────────────

    def create_directory(
        self,
        kb_id: str,
        name: str,
        parent_id: str | None = None,
    ) -> dict[str, Any]:
        """POST /knowledge/{id}/dirs/create — create a directory."""
        payload: dict[str, Any] = {"name": name}
        if parent_id:
            payload["parent_id"] = parent_id
        resp = self._http.post(
            f"/knowledge/{kb_id}/dirs/create",
            json=payload,
        )
        resp.raise_for_status()
        return resp.json()

    # ── KB management ───────────────────────────────────────────

    def reset_kb(
        self,
        kb_id: str,
        include_directories: bool = True,
    ) -> dict[str, Any]:
        """POST /knowledge/{id}/reset — reset the KB."""
        resp = self._http.post(
            f"/knowledge/{kb_id}/reset",
            params={"include_directories": include_directories},
        )
        resp.raise_for_status()
        return resp.json()

    def get_kb(self, kb_id: str) -> dict[str, Any]:
        """GET /knowledge/{id} — get KB metadata.

        Note: this endpoint returns metadata only. Its ``files`` field is a
        server-hydrated convenience that some Open WebUI versions return as
        null; use ``list_kb_files``/``count_kb_files`` for the file list.
        """
        resp = self._http.get(f"/knowledge/{kb_id}")
        resp.raise_for_status()
        return resp.json()

    def list_kb_files(self, kb_id: str) -> list[dict[str, Any]]:
        """GET /knowledge/{id}/files — list files in a KB."""
        resp = self._http.get(f"/knowledge/{kb_id}/files")
        resp.raise_for_status()
        data = resp.json()
        return data.get("items", [])

    def count_kb_files(self, kb_id: str) -> int:
        """GET /knowledge/{id}/files — total file count for a KB."""
        resp = self._http.get(f"/knowledge/{kb_id}/files")
        resp.raise_for_status()
        return resp.json().get("total", 0)
    def list_kbs(self) -> list[dict[str, Any]]:
        """GET /knowledge/ — list accessible knowledge bases."""
        resp = self._http.get("/knowledge/")
        resp.raise_for_status()
        data = resp.json()
        return data.get("items", []) if isinstance(data, dict) else data

    def create_kb(self, name: str, description: str = "") -> dict[str, Any]:
        """POST /knowledge/create — create a knowledge base."""
        resp = self._http.post(
            "/knowledge/create",
            json={"name": name, "description": description},
        )
        resp.raise_for_status()
        return resp.json()

    def list_directories(self, kb_id: str) -> list[dict[str, Any]]:
        """GET /knowledge/{id}/dirs — list KB directories (flat)."""
        resp = self._http.get(f"/knowledge/{kb_id}/dirs")
        resp.raise_for_status()
        data = resp.json()
        return data.get("items", data) if isinstance(data, dict) else data
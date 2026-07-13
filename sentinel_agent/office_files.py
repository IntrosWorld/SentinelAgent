from __future__ import annotations

import json
import os
import shutil
import subprocess
import tempfile
from datetime import datetime
from pathlib import Path
from typing import Any


class OfficeFileError(RuntimeError):
    pass


class OfficeFileManager:
    """Create and edit verified Office/PDF artifacts inside the workspace."""

    runtime_root = Path.home() / ".cache" / "codex-runtimes" / "codex-primary-runtime" / "dependencies"
    bundled_node = runtime_root / "node" / "bin" / "node.exe"
    bundled_node_modules = runtime_root / "node" / "node_modules"
    bundled_python = runtime_root / "python" / "python.exe"
    pdftoppm = runtime_root / "native" / "poppler" / "Library" / "bin" / "pdftoppm.exe"
    docx_renderer = (
        Path.home()
        / ".codex"
        / "plugins"
        / "cache"
        / "openai-primary-runtime"
        / "documents"
        / "26.727.11326"
        / "skills"
        / "documents"
        / "render_docx.py"
    )

    def __init__(self, workspace: Path | None = None):
        self.workspace = (workspace or Path.cwd()).resolve()
        self.artifacts = self.workspace / "artifacts"
        self.qa_root = self.artifacts / "_qa"
        self.backups = self.artifacts / "backups"
        for directory in (self.artifacts, self.qa_root, self.backups):
            directory.mkdir(parents=True, exist_ok=True)

    def _path(self, requested: str, extension: str, must_exist: bool = False) -> Path:
        raw = Path(requested)
        candidate = raw if raw.is_absolute() else self.artifacts / raw
        candidate = candidate.resolve()
        try:
            candidate.relative_to(self.workspace)
        except ValueError as exc:
            raise OfficeFileError("Files must remain inside the project workspace") from exc
        if candidate.suffix.lower() != extension:
            raise OfficeFileError(f"Expected a {extension} file: {candidate.name}")
        if must_exist and not candidate.is_file():
            raise OfficeFileError(f"File does not exist: {candidate}")
        candidate.parent.mkdir(parents=True, exist_ok=True)
        return candidate

    def _backup(self, path: Path) -> Path:
        stamp = datetime.now().strftime("%Y%m%d_%H%M%S_%f")
        backup = self.backups / f"{path.stem}_{stamp}{path.suffix}"
        shutil.copy2(path, backup)
        return backup

    def _qa_dir(self, output: Path) -> Path:
        return self.qa_root / f"{output.stem}_{output.suffix.lower().lstrip('.')}"

    def _spreadsheet_runtime(self) -> tuple[Path, Path]:
        if not self.bundled_node.is_file() or not self.bundled_node_modules.is_dir():
            raise OfficeFileError("Bundled spreadsheet artifact runtime is unavailable")
        worker_dir = Path(__file__).parent / "workers"
        link = worker_dir / "node_modules"
        if not link.exists():
            subprocess.run(
                ["cmd", "/c", "mklink", "/J", str(link), str(self.bundled_node_modules)],
                check=True,
                capture_output=True,
                text=True,
            )
        return self.bundled_node, worker_dir / "spreadsheet_worker.mjs"

    def _run_spreadsheet(self, payload: dict[str, Any]) -> dict[str, Any]:
        node, worker = self._spreadsheet_runtime()
        with tempfile.NamedTemporaryFile(
            mode="w", suffix=".json", encoding="utf-8", delete=False, dir=self.qa_root
        ) as stream:
            json.dump(payload, stream)
            payload_path = Path(stream.name)
        try:
            result = subprocess.run(
                [str(node), str(worker), str(payload_path)],
                cwd=worker.parent,
                check=False,
                capture_output=True,
                text=True,
                timeout=180,
            )
        finally:
            payload_path.unlink(missing_ok=True)
        line = next(
            (item for item in reversed(result.stdout.splitlines()) if item.startswith("SENTINEL_RESULT=")),
            None,
        )
        if line is None:
            raise OfficeFileError(result.stderr or result.stdout or "Spreadsheet worker returned no verification result")
        parsed = json.loads(line.removeprefix("SENTINEL_RESULT="))
        if not Path(parsed["output_path"]).is_file() or not Path(parsed["preview_path"]).is_file():
            raise OfficeFileError("Spreadsheet export or visual preview is missing")
        parsed["render_ok"] = True
        return parsed

    def create_xlsx(
        self,
        filename: str,
        title: str = "SentinelAgent Workbook",
        sheet_name: str = "Sheet1",
        headers: list[str] | None = None,
        rows: list[list[Any]] | None = None,
    ) -> dict[str, Any]:
        output = self._path(filename, ".xlsx")
        qa_dir = self._qa_dir(output)
        qa_dir.mkdir(parents=True, exist_ok=True)
        return self._run_spreadsheet(
            {
                "operation": "create",
                "output_path": str(output),
                "preview_path": str(qa_dir / "preview.png"),
                "qa_dir": str(qa_dir),
                "title": title,
                "sheet_name": sheet_name,
                "headers": headers or ["Item", "Value"],
                "rows": rows or [["Example", 1]],
            }
        )

    def edit_xlsx(
        self, filename: str, sheet_name: str, updates: list[dict[str, Any]]
    ) -> dict[str, Any]:
        output = self._path(filename, ".xlsx", must_exist=True)
        backup = self._backup(output)
        qa_dir = self._qa_dir(output)
        qa_dir.mkdir(parents=True, exist_ok=True)
        result = self._run_spreadsheet(
            {
                "operation": "edit",
                "input_path": str(output),
                "output_path": str(output),
                "preview_path": str(qa_dir / "preview.png"),
                "qa_dir": str(qa_dir),
                "sheet_name": sheet_name,
                "updates": updates,
            }
        )
        result["backup_path"] = str(backup)
        return result

    def _office_python(self) -> Path:
        if not self.bundled_python.is_file():
            raise OfficeFileError("Bundled document/PDF Python runtime is unavailable")
        return self.bundled_python

    def create_docx(
        self, filename: str, title: str, paragraphs: list[str]
    ) -> dict[str, Any]:
        output = self._path(filename, ".docx")
        payload = {
            "operation": "create_docx",
            "output_path": str(output),
            "title": title,
            "paragraphs": paragraphs,
        }
        return self._run_office_worker(payload, output)

    def append_docx(self, filename: str, paragraphs: list[str]) -> dict[str, Any]:
        output = self._path(filename, ".docx", must_exist=True)
        backup = self._backup(output)
        result = self._run_office_worker(
            {"operation": "append_docx", "output_path": str(output), "paragraphs": paragraphs},
            output,
        )
        result["backup_path"] = str(backup)
        return result

    def create_pdf(
        self, filename: str, title: str, paragraphs: list[str]
    ) -> dict[str, Any]:
        output = self._path(filename, ".pdf")
        return self._run_office_worker(
            {
                "operation": "create_pdf",
                "output_path": str(output),
                "title": title,
                "paragraphs": paragraphs,
            },
            output,
        )

    def append_pdf(self, filename: str, title: str, paragraphs: list[str]) -> dict[str, Any]:
        output = self._path(filename, ".pdf", must_exist=True)
        backup = self._backup(output)
        result = self._run_office_worker(
            {
                "operation": "append_pdf",
                "output_path": str(output),
                "title": title,
                "paragraphs": paragraphs,
            },
            output,
        )
        result["backup_path"] = str(backup)
        return result

    def _run_office_worker(self, payload: dict[str, Any], output: Path) -> dict[str, Any]:
        worker = Path(__file__).parent / "workers" / "office_worker.py"
        result = subprocess.run(
            [str(self._office_python()), str(worker), json.dumps(payload)],
            cwd=self.workspace,
            check=True,
            capture_output=True,
            text=True,
            timeout=120,
        )
        metadata = json.loads(result.stdout.strip().splitlines()[-1])
        qa_dir = self._qa_dir(output)
        qa_dir.mkdir(parents=True, exist_ok=True)
        if output.suffix.lower() == ".docx" and self.docx_renderer.is_file():
            render = subprocess.run(
                [
                    str(self._office_python()),
                    str(self.docx_renderer),
                    str(output),
                    "--output_dir",
                    str(qa_dir),
                ],
                capture_output=True,
                text=True,
                timeout=120,
            )
            pages = sorted(qa_dir.glob("page-*.png"))
            metadata["preview_paths"] = [str(page) for page in pages]
            metadata["render_ok"] = render.returncode == 0 and bool(pages)
        elif output.suffix.lower() == ".pdf" and self.pdftoppm.exists():
            prefix = qa_dir / "page"
            render = subprocess.run(
                [str(self.pdftoppm), "-png", str(output), str(prefix)],
                capture_output=True,
                text=True,
                timeout=120,
            )
            pages = sorted(qa_dir.glob("page-*.png"))
            metadata["preview_paths"] = [str(page) for page in pages]
            metadata["render_ok"] = render.returncode == 0 and bool(pages)
        return metadata

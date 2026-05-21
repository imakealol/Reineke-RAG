"""Convert legacy Microsoft Office formats (.doc / .xls) to modern XML
formats via LibreOffice in headless mode.

LibreOffice is optional — if the binary is missing we raise a clear
:class:`OfficeConversionError` so the caller can mark the document as
``failed`` and continue with the rest of the batch.
"""

from __future__ import annotations

import shutil
import subprocess
from pathlib import Path
from typing import Optional

from .config import settings
from .utils import get_logger

log = get_logger(__name__)


class OfficeConversionError(RuntimeError):
    pass


def libreoffice_available() -> bool:
    return shutil.which(settings.SOFFICE_BIN) is not None


def _run_soffice(src: Path, target_format: str, outdir: Path) -> Path:
    if not libreoffice_available():
        if target_format == "docx":
            raise OfficeConversionError(
                "Legacy .doc requires LibreOffice for conversion."
            )
        if target_format == "xlsx":
            raise OfficeConversionError(
                "Legacy .xls requires LibreOffice for conversion."
            )
        raise OfficeConversionError(
            f"LibreOffice ({settings.SOFFICE_BIN}) is not installed."
        )

    outdir.mkdir(parents=True, exist_ok=True)
    cmd = [
        settings.SOFFICE_BIN,
        "--headless",
        "--norestore",
        "--nologo",
        "--nofirststartwizard",
        "--convert-to",
        target_format,
        "--outdir",
        str(outdir),
        str(src),
    ]
    log.info("Running LibreOffice conversion: %s", " ".join(cmd))

    try:
        proc = subprocess.run(
            cmd,
            check=False,
            capture_output=True,
            text=True,
            timeout=180,
        )
    except subprocess.TimeoutExpired as exc:
        raise OfficeConversionError(
            f"LibreOffice timed out converting {src.name}"
        ) from exc

    if proc.returncode != 0:
        raise OfficeConversionError(
            f"LibreOffice failed for {src.name}: "
            f"rc={proc.returncode}, stderr={proc.stderr.strip()}"
        )

    expected = outdir / f"{src.stem}.{target_format}"
    if not expected.exists():
        # Some soffice versions normalise the name differently — find it.
        candidates = list(outdir.glob(f"{src.stem}.*"))
        if not candidates:
            raise OfficeConversionError(
                f"Conversion produced no output for {src.name} "
                f"(searched {outdir})."
            )
        expected = candidates[0]
    return expected


def convert_doc_to_docx(src: Path, outdir: Optional[Path] = None) -> Path:
    """Convert a legacy `.doc` to `.docx`."""
    if src.suffix.lower() != ".doc":
        raise OfficeConversionError(f"Expected a .doc file, got {src.suffix}")
    out = outdir or settings.converted_dir
    return _run_soffice(src, "docx", out)


def convert_xls_to_xlsx(src: Path, outdir: Optional[Path] = None) -> Path:
    """Convert a legacy `.xls` to `.xlsx`."""
    if src.suffix.lower() != ".xls":
        raise OfficeConversionError(f"Expected a .xls file, got {src.suffix}")
    out = outdir or settings.converted_dir
    return _run_soffice(src, "xlsx", out)


def convert_odt_to_docx(src: Path, outdir: Optional[Path] = None) -> Path:
    """Convert an OpenDocument Text file (`.odt`) to `.docx` so we can
    reuse the existing python-docx loader. Requires LibreOffice."""
    if src.suffix.lower() != ".odt":
        raise OfficeConversionError(f"Expected a .odt file, got {src.suffix}")
    out = outdir or settings.converted_dir
    return _run_soffice(src, "docx", out)

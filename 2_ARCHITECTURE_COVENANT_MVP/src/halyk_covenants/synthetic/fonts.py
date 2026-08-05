import os
from dataclasses import dataclass
from pathlib import Path

from reportlab.pdfbase import pdfmetrics
from reportlab.pdfbase.ttfonts import TTFont


@dataclass(frozen=True)
class FontFamily:
    regular: str
    bold: str


def register_cyrillic_fonts() -> FontFamily:
    """Register deterministic DejaVu font names for native Cyrillic PDF text."""
    regular_name = "HalykDejaVu"
    bold_name = "HalykDejaVu-Bold"
    if regular_name in pdfmetrics.getRegisteredFontNames():
        return FontFamily(regular=regular_name, bold=bold_name)

    candidates = []
    override = os.environ.get("HALYK_DEJAVU_DIR")
    if override:
        candidates.append(Path(override))
    candidates += [
        # Container / Linux — the canonical environment, see Dockerfile (fonts-dejavu-core).
        Path("/usr/share/fonts/truetype/dejavu"),
        Path("/usr/share/fonts/dejavu"),
        Path("/usr/local/share/fonts/dejavu"),
        # macOS
        Path("/opt/homebrew/share/fonts"),
        Path("/usr/local/share/fonts"),
        # Windows — local development only.
        Path(os.environ.get("WINDIR", "C:/Windows")) / "Fonts",
        Path.home() / "AppData/Local/Microsoft/Windows/Fonts",
    ]
    for directory in candidates:
        regular_path = directory / "DejaVuSans.ttf"
        bold_path = directory / "DejaVuSans-Bold.ttf"
        if regular_path.exists() and bold_path.exists():
            pdfmetrics.registerFont(TTFont(regular_name, regular_path))
            pdfmetrics.registerFont(TTFont(bold_name, bold_path))
            pdfmetrics.registerFontFamily(
                "HalykDejaVu",
                normal=regular_name,
                bold=bold_name,
                italic=regular_name,
                boldItalic=bold_name,
            )
            return FontFamily(regular=regular_name, bold=bold_name)
    searched = "\n  ".join(str(directory) for directory in candidates)
    raise FileNotFoundError(
        "DejaVuSans.ttf and DejaVuSans-Bold.ttf are required for PDF generation.\n"
        "The canonical environment is the container, where fonts-dejavu-core provides them "
        "(see Dockerfile). To run outside the container, install the DejaVu fonts or point "
        "HALYK_DEJAVU_DIR at a directory containing both files.\n"
        f"Searched:\n  {searched}"
    )

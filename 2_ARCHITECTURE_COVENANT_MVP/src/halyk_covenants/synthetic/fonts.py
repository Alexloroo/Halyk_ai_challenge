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

    candidates = [
        Path("/usr/share/fonts/truetype/dejavu"),
        Path("/usr/local/share/fontss/dejavu"),
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
    raise FileNotFoundError("DejaVuSans.ttf and DejaVuSans-Bold.ttf are required for PDF generation")


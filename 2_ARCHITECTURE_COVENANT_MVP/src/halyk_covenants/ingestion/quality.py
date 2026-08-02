from decimal import Decimal

from pydantic import BaseModel, ConfigDict, Field

from halyk_covenants.domain import PageExtractionQuality
from halyk_covenants.observability import trace_stage


class NativePage(BaseModel):
    model_config = ConfigDict(extra="forbid")

    page: int = Field(ge=1)
    text: str
    image_count: int = Field(ge=0)
    table_count: int = Field(ge=0)
    width: float = Field(gt=0)
    height: float = Field(gt=0)


class PageQualityRouter:
    def __init__(self, native_text_min_chars: int = 80) -> None:
        if native_text_min_chars < 0:
            raise ValueError("native_text_min_chars cannot be negative")
        self.native_text_min_chars = native_text_min_chars

    @trace_stage("document.classify_page", run_type="tool")
    def classify(self, page: NativePage) -> PageExtractionQuality:
        readable_chars = sum(
            character.isprintable() and not character.isspace() for character in page.text
        )
        density = Decimal(readable_chars) / Decimal(str(page.width * page.height))
        reasons: list[str] = []
        if page.table_count > 0:
            route = "layout"
            confidence = Decimal("0.95")
            reasons.append("table layout detected")
        elif readable_chars >= self.native_text_min_chars:
            route = "native"
            confidence = (
                Decimal("0.99")
                if readable_chars >= self.native_text_min_chars * 2
                else Decimal("0.85")
            )
            reasons.append("sufficient native text")
        elif page.image_count > 0 or readable_chars > 0:
            route = "ocr"
            confidence = Decimal("0.90") if page.image_count else Decimal("0.70")
            reasons.append("insufficient native text")
        else:
            route = "failed"
            confidence = Decimal("0")
            reasons.append("no usable page content")
        return PageExtractionQuality(
            native_text_chars=readable_chars,
            text_density=density,
            image_count=page.image_count,
            table_count=page.table_count,
            route=route,
            confidence=confidence,
            reasons=reasons,
        )

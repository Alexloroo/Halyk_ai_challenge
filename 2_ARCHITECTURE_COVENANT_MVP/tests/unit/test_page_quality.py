from halyk_covenants.ingestion.quality import NativePage, PageQualityRouter


def test_empty_native_page_routes_to_ocr() -> None:
    quality = PageQualityRouter(native_text_min_chars=80).classify(
        NativePage(
            page=1,
            text="",
            image_count=1,
            table_count=0,
            width=595,
            height=842,
        )
    )

    assert quality.route == "ocr"
    assert quality.native_text_chars == 0


def test_readable_native_page_does_not_route_to_ocr() -> None:
    text = "Финансовые ковенанты. " * 10
    quality = PageQualityRouter(native_text_min_chars=80).classify(
        NativePage(
            page=1,
            text=text,
            image_count=0,
            table_count=1,
            width=595,
            height=842,
        )
    )

    assert quality.route == "native"
    assert quality.confidence > 0.8


def test_short_multicolumn_page_routes_to_layout() -> None:
    quality = PageQualityRouter(native_text_min_chars=80).classify(
        NativePage(
            page=2,
            text="Заёмщик | Порог",
            image_count=0,
            table_count=2,
            width=595,
            height=842,
        )
    )

    assert quality.route == "layout"

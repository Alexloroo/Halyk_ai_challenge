"""Transcriptions of image-only PDF pages.

Three documents in the archive carry data as embedded page images that
PyMuPDF's text extraction cannot see:

  * ``f3fa6d20c8a1.pdf`` — the entire KYC dossier for Taraz Cement Works JSC
    (ACC-7806) is scanned; without it the scenario has no related parties;
  * ``63e162bd710b.pdf`` — page 2 of the ACC-7802 KYC holds the beneficial
    ownership table;
  * ``aaf665cbc612.pdf`` — page 2 of the ACC-7809 KYC holds the subsidiary
    security-coverage table that defines which subsidiaries are unrestricted.

No OCR engine ships with this package, so the pages were rendered to PNG and
transcribed by hand. The text below preserves the one-cell-per-line layout the
regex parsers in `parties.py` expect. `docs.load_documents` appends the
transcription to the extracted text of the matching file.
"""

SCANNED_PAGES: dict[str, str] = {
    # KYC dossier, Taraz Cement Works JSC (ACC-7806) — fully scanned document.
    "f3fa6d20c8a1.pdf": """\
Досье «Знай своего клиента» (KYC)
Проверка связанных сторон · Taraz Cement Works JSC
Организация
Taraz Cement Works JSC
Счёт
ACC-7806
Регистрационный номер досье
KYC-ACC-7806-2025
Дата проверки
31 декабря 2025 года
Бенефициарное владение и контроль
Организация
Доля голосующих прав
"Taraz Holding Group" LLP
46.8%
Taraz Kiln Services LLP
38.1%
Ural Grinding Works LLP
11.5%
Организации, в которых Группа владеет 40.0% и более голосующих прав, признаются
связанными сторонами для целей Договора.
""",
    # KYC ACC-7802 (Almaty Cold Chain JSC), page 2 — beneficial ownership table.
    "63e162bd710b.pdf": """\
Бенефициарное владение и контроль
Организация
Доля голосующих прав
Almaty Chill Logistics LLP
8.6%
Tien Shan Advisory Bureau
23.4%
Zhetysu Capital Partners LLP
31.2%
Организации, в которых Группа владеет 25.0% и более голосующих прав, признаются
связанными сторонами для целей Договора.
""",
    # KYC ACC-7809 (Zhezkazgan Mining Services JSC), page 2 — pledge coverage.
    "aaf665cbc612.pdf": """\
Обеспечительное покрытие дочерних организаций
Дочерняя организация
Доля активов в залоге
Zhezkazgan Conveyor Assets LLP
87.6%
Zhezkazgan Processing Holdings LLP
11.4%
Дочерние организации, у которых доля активов в залоге ниже 50.0%, находятся вне периметра
обеспечения и для целей Договора рассматриваются как неограниченные.
""",
}

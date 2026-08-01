# Synthetic Covenant Q&A

> These answers are derived from golden CovenantSpec files; PDF extraction is not scored.

## ALPHA-SUM-APRIL

- **Question:** Соблюдён ли месячный лимит исходящих KZT-платежей Alpha Trade за апрель?
- **Borrower:** `B001`
- **Evaluation date:** `2026-04-30`
- **Covenant:** `COV-ALPHA-SUM`
- **Source PDF:** `alpha_trade_contract.pdf`
- **Verdict:** `violated`
- **Number:** `16000000.000000`
- **Evidence transaction:** `None`
- **Expected status:** `success`
- **Explanation:** 5M + 6M + 5M = 16M KZT; USD row and May row are excluded.

## ALPHA-MAX-APRIL

- **Question:** Каков максимальный исходящий KZT-перевод Alpha Trade за апрель?
- **Borrower:** `B001`
- **Evaluation date:** `2026-04-30`
- **Covenant:** `COV-ALPHA-MAX`
- **Source PDF:** `alpha_trade_contract.pdf`
- **Verdict:** `violated`
- **Number:** `6000000.000000`
- **Evidence transaction:** `A002`
- **Expected status:** `success`
- **Explanation:** A002 is the largest matching transfer and exceeds 5M KZT.

## ALPHA-COUNT-TRIGGER

- **Question:** Какая операция третьей превысила месячный лимит операций Alpha Trade свыше 4M?
- **Borrower:** `B001`
- **Evaluation date:** `2026-04-30`
- **Covenant:** `COV-ALPHA-COUNT`
- **Source PDF:** `alpha_trade_contract.pdf`
- **Verdict:** `violated`
- **Number:** `3`
- **Evidence transaction:** `A003`
- **Expected status:** `success`
- **Explanation:** A001, A002, and A003 match; A003 is the threshold-crossing third transaction.

## ALPHA-MIN-INCOMING

- **Question:** Соблюдён ли минимальный размер входящего пополнения Alpha Trade за апрель?
- **Borrower:** `B001`
- **Evaluation date:** `2026-04-30`
- **Covenant:** `COV-ALPHA-MIN`
- **Source PDF:** `alpha_trade_contract.pdf`
- **Verdict:** `complied`
- **Number:** `2000000.000000`
- **Evidence transaction:** `None`
- **Expected status:** `success`
- **Explanation:** The only matching incoming KZT transaction equals the 2M boundary.

## BETA-AVG-APRIL

- **Question:** Превысил ли средний исходящий платёж Beta Logistics 4M KZT в апреле?
- **Borrower:** `B002`
- **Evaluation date:** `2026-04-30`
- **Covenant:** `COV-BETA-AVG`
- **Source PDF:** `borrower_limits_appendix.pdf`
- **Verdict:** `complied`
- **Number:** `4000000.000000`
- **Evidence transaction:** `None`
- **Expected status:** `success`
- **Explanation:** (3M + 3M + 6M) / 3 equals the permitted 4M boundary.

## BETA-SUM-BOUNDARY

- **Question:** Соблюдён ли суммарный лимит Beta Logistics за апрель?
- **Borrower:** `B002`
- **Evaluation date:** `2026-04-30`
- **Covenant:** `COV-BETA-SUM`
- **Source PDF:** `borrower_limits_appendix.pdf`
- **Verdict:** `complied`
- **Number:** `12000000.000000`
- **Evidence transaction:** `None`
- **Expected status:** `success`
- **Explanation:** The total equals 12M and <= is satisfied at the boundary.

## ALPHA-SUM-EMPTY

- **Question:** Каков исходящий объём Alpha Trade за июнь при отсутствии операций?
- **Borrower:** `B001`
- **Evaluation date:** `2026-06-30`
- **Covenant:** `COV-ALPHA-SUM`
- **Source PDF:** `alpha_trade_contract.pdf`
- **Verdict:** `complied`
- **Number:** `0.000000`
- **Evidence transaction:** `None`
- **Expected status:** `success`
- **Explanation:** Empty SUM has explicit zero semantics.

## ALPHA-MAX-EMPTY

- **Question:** Каков максимальный перевод Alpha Trade за июнь при отсутствии операций?
- **Borrower:** `B001`
- **Evaluation date:** `2026-06-30`
- **Covenant:** `COV-ALPHA-MAX`
- **Source PDF:** `alpha_trade_contract.pdf`
- **Verdict:** `unknown`
- **Number:** `None`
- **Evidence transaction:** `None`
- **Expected status:** `partial`
- **Explanation:** Empty MAX is undefined and must remain an explicit partial result.

## GAMMA-SUM-DUPLICATE

- **Question:** Каков исходящий объём 000777 с сохранённой дублированной строкой?
- **Borrower:** `000777`
- **Evaluation date:** `2026-04-30`
- **Covenant:** `COV-GAMMA-SUM`
- **Source PDF:** `borrower_limits_appendix.pdf`
- **Verdict:** `violated`
- **Number:** `7000000.000000`
- **Evidence transaction:** `None`
- **Expected status:** `success`
- **Explanation:** 1M + 2M + 2M + duplicated 2M = 7M; ingestion does not silently deduplicate.

## BETA-MAX-ISOLATION

- **Question:** Не смешивает ли расчёт максимума Beta Logistics операции других заёмщиков?
- **Borrower:** `B002`
- **Evaluation date:** `2026-04-30`
- **Covenant:** `COV-BETA-MAX`
- **Source PDF:** `borrower_limits_appendix.pdf`
- **Verdict:** `complied`
- **Number:** `6000000.000000`
- **Evidence transaction:** `None`
- **Expected status:** `success`
- **Explanation:** Only B002 rows are considered; its maximum is 6M against a 7M limit.

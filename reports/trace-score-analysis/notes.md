# Source and methodology notes

- Controlling score source: `trace/14_ground_truth/comparison.json` compared with `data/raw/ground_truth.json`.
- Every penalized cell was traced backward through evaluation, formulas, related-party extraction, document selection, and parsed PDF text until the first unsupported value or semantic loss.
- The score path is unweighted because the supplied trace exposes only the unweighted local score; challenge complexity weights are not present in the dataset.
- Chart map: section `Потеря сконцентрирована в пяти механизмах`; question `какие механизмы возвращают больше всего score`; family/type `comparison / bar`; fields `mechanism`, `gain`, with `confidence` and `cells` retained for tooltips; claim `four high-confidence mechanisms recover 3.4 points`; palette `single-root preferred`; delivery `report.html`.
- P4 / 6.3 is intentionally labeled low confidence: the calculation, selected documents, KYC relationship, and agreement threshold agree with each other, while the public ground truth disagrees on status.
- P1 / 6.1 and P2 / 6.2 have non-exact evidence fields but lose no points under the challenge rule for cells whose expected evidence is `null`.

from halyk_covenants.borrowers import BorrowerClaim, BorrowerResolver
from halyk_covenants.domain import Borrower


def borrowers() -> list[Borrower]:
    return [
        Borrower(
            borrower_id="B001",
            canonical_name='ТОО "Альфа Трейд"',
            identifiers={"BIN": "9901"},
            aliases=["ALFA TRADE LLP"],
        ),
        Borrower(
            borrower_id="B002",
            canonical_name="Beta Logistics LLP",
            identifiers={"BIN": "9902"},
            aliases=["Бета Лог."],
        ),
        Borrower(borrower_id="B003", canonical_name="Alfa Trading LLP"),
    ]


def test_exact_identifier_cannot_be_overridden_by_better_name_score() -> None:
    resolver = BorrowerResolver(borrowers())

    result = resolver.resolve(BorrowerClaim(identifiers={"BIN": "9901"}, name="Beta Logistics"))

    assert result.borrower_ids == ["B001"]
    assert result.status == "resolved_exact"
    assert result.matched_by == "identifier:BIN"


def test_explicit_alias_resolves_before_fuzzy_matching() -> None:
    result = BorrowerResolver(borrowers()).resolve(BorrowerClaim(name="ALFA TRADE LLP"))

    assert result.borrower_ids == ["B001"]
    assert result.status == "resolved_alias"


def test_close_fuzzy_candidates_remain_ambiguous() -> None:
    resolver = BorrowerResolver(borrowers(), fuzzy_threshold=60, ambiguity_margin=10)

    result = resolver.resolve(BorrowerClaim(name="Alfa Trade"))

    assert result.status == "ambiguous"
    assert result.borrower_ids == []
    assert {candidate.borrower_id for candidate in result.candidates[:2]} == {"B001", "B003"}


def test_unknown_claim_remains_unresolved() -> None:
    result = BorrowerResolver(borrowers()).resolve(BorrowerClaim(name="Completely Unknown Entity"))

    assert result.status == "unresolved"
    assert result.borrower_ids == []

"""PDP donor bench pooling (app.pdp.service.donor_benches) — DB-free."""

from app.pdp.service import donor_benches


def test_wcxy_and_wca1_pool_both_ways() -> None:
    assert donor_benches("WCXY") == ["WCA_1", "WCXY"]
    assert donor_benches("WCA_1") == ["WCA_1", "WCXY"]


def test_other_benches_stay_exact() -> None:
    assert donor_benches("WCA_2") == ["WCA_2"]
    assert donor_benches("WCB_1") == ["WCB_1"]
    assert donor_benches(None) == []

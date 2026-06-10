"""identity — DOB-first name → SAMS resolution (offline + :8080 scorer path)."""
from __future__ import annotations


def _roster():
    return [
        {"player_id": 2893, "full_name": "Mohamed Noufal", "dob": "2005-08-14",
         "sport": "Athletics", "photo_url": None, "mrn": None},
        {"player_id": 3247, "full_name": "Mohammed Ali", "dob": "2014-03-05",
         "sport": "Athletics", "photo_url": None, "mrn": None},
        {"player_id": 2892, "full_name": "Anas Fadeel", "dob": "2004-01-21",
         "sport": "Athletics", "photo_url": None, "mrn": None},
        {"player_id": 3141, "full_name": "Abderrahman Alsaleck", "dob": "1995-09-05",
         "sport": "Athletics", "photo_url": None, "mrn": None},
        {"player_id": 9001, "full_name": "Khalid Saad", "dob": "2008-02-02",
         "sport": "Fencing", "photo_url": None, "mrn": None},
    ]


def test_helpers():
    from aspire_data.identity import name_tokens, is_placeholder_dob, sport_agrees
    assert name_tokens("Al-Sahoti Bin Saleh") == {"sahoti", "saleh"}
    assert is_placeholder_dob("2007-01-01") is True
    assert is_placeholder_dob("2007-08-14") is False
    assert sport_agrees("Jumps", "Athletics") is True       # both normalise to Athletics
    assert sport_agrees("Swimming", "Athletics") is False
    assert sport_agrees("Development 1", "Athletics") is None  # program label → unknown


def test_resolve_long_name_caught_by_dob(monkeypatch):
    from aspire_data import identity
    res = identity.resolve_to_sams(
        [{"name": "Mohammed Aly Abdelmonem Monsef Noufal", "dob": "2005-08-14", "sport": "Jumps"}],
        roster=_roster(), use_match_api=False)[0]
    assert res["player_id"] == 2893 and res["verdict"] == "auto"


def test_resolve_age_gap_no_link():
    from aspire_data.identity import resolve_to_sams
    res = resolve_to_sams([{"name": "Mohammed Khalifa", "dob": "2004-05-01", "sport": "Athletics"}],
                          roster=_roster(), use_match_api=False)[0]
    assert res["player_id"] is None                          # only candidate born 10y later


def test_resolve_sport_conflict_not_auto():
    from aspire_data.identity import resolve_to_sams
    res = resolve_to_sams([{"name": "Khalid Saad", "dob": "2008-02-02", "sport": "Athletics"}],
                          roster=_roster(), use_match_api=False)[0]
    assert res["player_id"] is None                          # roster Khalid Saad is Fencing


def test_resolve_common_firstname_only_is_review():
    from aspire_data.identity import resolve_to_sams
    res = resolve_to_sams([{"name": "Abderrahman Samba", "dob": "1995-09-05", "sport": "Athletics"}],
                          roster=_roster(), use_match_api=False)[0]
    assert res["player_id"] is None and res["verdict"] == "review"


def test_resolve_distinctive_surname_auto():
    from aspire_data.identity import resolve_to_sams
    res = resolve_to_sams([{"name": "Anas Meerghani Ali Fadil", "dob": "2004-01-21", "sport": "Athletics"}],
                          roster=_roster(), use_match_api=False)[0]
    assert res["player_id"] == 2892 and res["verdict"] == "auto"


def test_resolve_no_match():
    from aspire_data.identity import resolve_to_sams
    res = resolve_to_sams([{"name": "Zzzzqq Nobody"}], roster=_roster(), use_match_api=False)[0]
    assert res["verdict"] == "no_match" and res["candidate"] is None


def test_fetch_roster_maps_sams_fields():
    from aspire_data.identity import fetch_roster

    class FakeClient:
        def search(self, q):
            assert q == ""                                   # empty query → full roster
            return [{"playerId": 7, "fullName": "Test Athlete", "dateOfBirth": "2005-01-01",
                     "currentSportName": "Swimming", "mrn": 1234, "profileImageUrl": "u"}]

    r = fetch_roster(FakeClient())
    assert r == [{"player_id": 7, "full_name": "Test Athlete", "dob": "2005-01-01",
                  "sport": "Swimming", "mrn": "1234", "photo_url": "u"}]


def test_match_api_is_the_scorer_when_enabled(monkeypatch):
    """When the :8080 engine is enabled it provides the authoritative name score
    (rapidfuzz only blocks). Mock it to confirm its score drives the verdict."""
    from aspire_data import identity
    monkeypatch.setattr(identity, "match_pairs", lambda pairs, **kw: {p: 97 for p in pairs})
    res = identity.resolve_to_sams([{"name": "Mohamed Noufal", "sport": "Athletics"}],
                                   roster=_roster(), use_match_api=True)[0]
    assert res["player_id"] == 2893
    assert res["candidate"]["name_score"] == 97              # came from the (mocked) :8080 engine

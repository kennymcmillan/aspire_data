"""SamsClient.competition_results — SAMS track/field results in Athletics_Combined shape."""
from __future__ import annotations


def _fake_get(path, params=None):
    pid = (params or {}).get("PlayerId")
    track = {
        1: [{"competitionEventId": 10, "playerId": 1, "timeResult": "00:00:09.3200000",
             "resultType": "60m", "windSpeed": 0.4, "place": 2, "isActive": True,
             "isValidResult": True, "isPersonalBest": True}],
        2: [{"competitionEventId": 11, "playerId": 2, "timeResult": "00:02:05.3300000",
             "resultType": "800m", "isActive": True},
            {"competitionEventId": 12, "playerId": 2, "timeResult": None, "dns": True,
             "resultType": "60m", "isActive": True}],
    }
    field = {
        # Aspire Invitational 6 shape: height only, bestResult empty, not marked valid
        1: [{"competitionEventId": 39, "playerId": 1, "bestResult": None, "height": 3.5,
             "attemptCleared": True, "isValidResult": False, "isActive": True}],
        # attempt rows for one event -> best mark kept; an inactive row is ignored
        2: [{"competitionEventId": 20, "playerId": 2, "bestResult": 4.1, "isActive": True},
            {"competitionEventId": 20, "playerId": 2, "bestResult": 4.35, "isActive": True},
            {"competitionEventId": 20, "playerId": 2, "bestResult": 9.9, "isActive": False},
            {"competitionEventId": 21, "playerId": 2, "bestResult": None, "isActive": True}],
    }
    events = {
        10: {"competitionId": 100, "eventTypeName": "60 Metres", "eventDate": "2026-01-07"},
        11: {"competitionId": 100, "eventTypeName": "800 Metres", "eventDate": "2026-01-07"},
        12: {"competitionId": 100, "eventTypeName": "60 Metres", "eventDate": "2026-01-08"},
        39: {"competitionId": 7967, "eventTypeName": "Pole Vault", "eventDate": "2026-05-12",
             "venueLocation": "Doha, null", "ageCategoryName": "Open", "gender": "M"},
        20: {"competitionId": 100, "eventTypeName": "Long Jump", "eventDate": "2026-01-07"},
        21: {"competitionId": 100, "eventTypeName": "Shot Put", "eventDate": "2026-01-07"},
    }
    masters = {100: {"name": "Aspire Indoor Invitational 1"}, 7967: {"name": "Aspire Invitational 6"}}
    if path.endswith("CompetitionTrackResults/Search"):
        return track.get(pid, [])
    if path.endswith("CompetitionFieldResults/Search"):
        return field.get(pid, [])
    if "/CompetitionEvent/" in path:
        return dict(events[int(path.rsplit("/", 1)[1])])
    if "/CompetitionMaster/" in path:
        return masters[int(path.rsplit("/", 1)[1])]
    raise AssertionError(path)


def _rows(mock_httpx, monkeypatch, ids=(1, 2)):
    from aspire_data.sams import SamsClient
    s = SamsClient()
    monkeypatch.setattr(s, "_get", _fake_get)
    return {(r["sams_player_id"], r["sams_event_id"]): r for r in s.competition_results(ids)}


def test_invitational_6_height_only_row(mock_httpx, monkeypatch):
    r = _rows(mock_httpx, monkeypatch)[(1, 39)]
    assert r["Result"] == r["Result_clean"] == "3.50"
    assert r["Competition"] == "Aspire Invitational 6"
    assert r["Event_standard"] == "Pole Vault" and r["Start_Date"] == "2026-05-12"
    assert r["Venue"] == "Doha" and r["Event_category"] == "Open M"
    assert r["sams_valid"] is False and r["Wind"] is None


def test_track_time_wind_place_pb(mock_httpx, monkeypatch):
    rows = _rows(mock_httpx, monkeypatch)
    r = rows[(1, 10)]
    assert (r["Result"], r["Event_standard"], r["Wind"], r["Position"], r["Personal_Best"]) == \
        ("9.32", "60m", "+0.4", "2", "1")
    assert rows[(2, 11)]["Result"] == "2:05.33"
    assert rows[(2, 12)]["Result"] == "DNS"


def test_field_best_of_attempts_and_no_mark_dropped(mock_httpx, monkeypatch):
    rows = _rows(mock_httpx, monkeypatch)
    assert rows[(2, 20)]["Result"] == "4.35"      # best active attempt, inactive 9.9 ignored
    assert (2, 21) not in rows                    # no mark, no flag -> no row


def test_failed_player_read_is_skipped(mock_httpx, monkeypatch):
    from aspire_data.sams import SamsClient, SamsError
    s = SamsClient()

    def get(path, params=None):
        if (params or {}).get("PlayerId") == 2:
            raise SamsError("SAMS 500")
        return _fake_get(path, params)
    monkeypatch.setattr(s, "_get", get)
    out = s.competition_results([1, 2])
    assert {r["sams_player_id"] for r in out} == {1}


def test_fmt_time():
    from aspire_data.sams import _fmt_time, _secs
    assert _secs("00:00:09.3200000") == 9.32 and _secs(None) is None
    assert (_fmt_time(9.32), _fmt_time(125.33), _fmt_time(3725.0)) == ("9.32", "2:05.33", "1:02:05.00")

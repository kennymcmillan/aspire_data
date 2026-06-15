"""ingest — the generic messy-data lane (columns, validation, ledger, ladder, writes)."""
from __future__ import annotations

import pandas as pd


def test_sanitize_col_reserved_and_collisions():
    from aspire_data.ingest import RESERVED_COLS, sanitize_col
    used = set(RESERVED_COLS)
    assert sanitize_col("ID", used=used) == "id_x"            # SmartSpeed literal ID col
    assert sanitize_col("Height (cm)") == "height_cm"
    assert sanitize_col("5 Bound Standing") == "c_5_bound_standing"
    assert sanitize_col("Tf / Tc") == "tf_tc"


def test_infer_sql_type_sizing():
    from aspire_data.ingest import infer_sql_type
    assert infer_sql_type(pd.Series([1.5, 2.0, None])) == "DOUBLE"
    assert infer_sql_type(pd.Series(["1:23.4", "1:24.0"])) == "VARCHAR(40)"
    assert infer_sql_type(pd.Series([None, None])) == "VARCHAR(100)"   # empty col: row-cap budget
    assert infer_sql_type(pd.Series(["x" * 300])) == "TEXT"


def test_build_ddl_head_and_uniques():
    from aspire_data.ingest import build_ddl
    ddl = build_ddl("aspire_data_demo", {"tw3": "DOUBLE"})
    assert "row_uid VARCHAR(120) NOT NULL" in ddl
    assert "UNIQUE KEY uq_row (row_uid)" in ddl
    assert "`tw3` DOUBLE NULL" in ddl
    assert "sams_id INT NULL" in ddl


def test_deterministic_id_stable():
    from aspire_data.ingest import deterministic_id
    a = deterministic_id("Mo Noufal", "2024-09-19", "L2F")
    assert a == deterministic_id("Mo Noufal", "2024-09-19", "L2F")
    assert a != deterministic_id("Mo Noufal", "2024-09-20", "L2F")


def test_validate_ranges_nulls_and_reports():
    from aspire_data.ingest import validate_ranges
    df = pd.DataFrame({"height_cm": [165.0, 999.0, None], "weight_kg": [60.0, 61.0, 62.0]})
    rep = validate_ranges(df, {"height_cm": (100, 230), "weight_kg": (25, 160)})
    assert df["height_cm"].iloc[1] is None or pd.isna(df["height_cm"].iloc[1])
    row = rep[rep.column == "height_cm"].iloc[0]
    assert row.n_out_of_range == 1 and row.n_checked == 2
    assert rep[rep.column == "weight_kg"].iloc[0].n_out_of_range == 0


def test_decision_ledger_roundtrip(tmp_path):
    from aspire_data.ingest import DecisionLedger
    p = tmp_path / "decisions.csv"
    led = DecisionLedger(p)
    led.record("Saleh Al-Sadi", None, "no", "2026-06-10")
    led.record("Ahmed Mohamed", 3391, "yes", "2026-06-10")
    led.record("MEAN (n=5)", None, "drop", "2026-06-10")
    led2 = DecisionLedger(p)                                   # reload from disk
    assert led2.get("Saleh Al-Sadi") == (None, "no")
    assert led2.get("Ahmed Mohamed") == (3391, "yes")
    assert "MEAN (n=5)" in led2 and led2.get("MEAN (n=5)")[1] == "drop"
    assert led2.get("Undecided Person") is None


def _identifiers():
    return [{"sams_player_id": 2854, "sams_name": "Saad Elarabi",
             "sams_mrn": "20053255", "smartabase_name": "Saad Elarabi"}]


def _roster():
    return [{"player_id": 2893, "full_name": "Mohamed Noufal", "dob": "2005-08-14",
             "sport": "Athletics", "photo_url": None, "mrn": None}]


def test_ladder_exact_then_mrn_then_ledger_then_resolver(tmp_path):
    from aspire_data.ingest import DecisionLedger, resolve_names_ladder
    led = DecisionLedger(tmp_path / "d.csv")
    led.record("Old Athlete", None, "no", "2026-06-10")
    idents = _identifiers()
    exact = {str(r["smartabase_name"]): r for r in idents}
    res = resolve_names_ladder(
        ["Saad Elarabi",                       # 1: exact smartabase join
         "Profile MRN Kid",                    # 2: MRN join
         "Old Athlete",                        # 3: ledger says no
         "Mohammed Aly Abdelmonem Monsef Noufal",  # 4: resolver (DOB-first)
         "a_Test Dummy"],                      # drop set
        exact_map=exact,
        mrn_lookup={"Profile MRN Kid": "20053255"},
        dob_lookup={"Mohammed Aly Abdelmonem Monsef Noufal": "2005-08-14"},
        sport_lookup={"Mohammed Aly Abdelmonem Monsef Noufal": "Jumps"},
        drop_names={"a_Test Dummy"},
        ledger=led, identifiers=idents, roster=_roster(), use_match_api=False)
    assert res["Saad Elarabi"]["verdict"] == "exact" and res["Saad Elarabi"]["sams_id"] == 2854
    assert res["Profile MRN Kid"]["verdict"] == "mrn_exact"
    assert res["Old Athlete"]["verdict"] == "manual_rejected" and res["Old Athlete"]["sams_id"] is None
    long = res["Mohammed Aly Abdelmonem Monsef Noufal"]
    assert long["verdict"] == "auto" and long["sams_id"] == 2893
    assert res["a_Test Dummy"]["verdict"] == "dummy"


def test_chunked_upsert_width_aware():
    from aspire_data.ingest import chunked_upsert

    calls = []

    class FakeApi:
        def tool_write(self, name, **params):
            calls.append(len(params["records"]))

    recs = [{f"c{i}": i for i in range(100)} for _ in range(450)]  # wide rows
    n = chunked_upsert(FakeApi(), "t", recs, key_columns=["row_uid"], api_key="k")
    assert n == 450
    assert max(calls) <= 200 and sum(calls) == 450               # 20000/100 = 200-row chunks


def test_sql_literal():
    from aspire_data.sports_api import sql_literal
    assert sql_literal(None) == "NULL"
    assert sql_literal(True) == "1" and sql_literal(False) == "0"
    assert sql_literal(3) == "3"
    assert sql_literal("a'b") == "'a''b'"
    assert sql_literal("a\\b") == "'a\\\\b'"


def test_replace_children_insert_before_delete():
    from aspire_data.ingest import replace_children
    calls = []

    class FakeApi:
        def table(self, table, **k):
            return [{"id": 50}]            # watermark

        def tool_write(self, name, **params):
            calls.append((name, params.get("sql", "")))

    n = replace_children(FakeApi(), "t", "test_uuid", "u1", [{"a": 1}, {"a": 2}], api_key="k")
    assert n == 2
    # bulk_insert MUST precede the delete, and the delete uses the watermark + quoted key
    assert [c[0] for c in calls] == ["bulk_insert", "execute_write_sql"]
    assert "id <= 50" in calls[1][1] and "test_uuid = 'u1'" in calls[1][1]


def test_replace_children_empty_rows_still_deletes():
    from aspire_data.ingest import replace_children
    calls = []

    class FakeApi:
        def table(self, table, **k):
            return []

        def tool_write(self, name, **params):
            calls.append(name)

    n = replace_children(FakeApi(), "t", "test_uuid", "u1", [], api_key="k")
    assert n == 0 and calls == ["execute_write_sql"]   # no insert, just the stale-delete

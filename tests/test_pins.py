"""pins — drift detection + bump across deploy-repo requirements files."""
from __future__ import annotations

CUR = {
    "aspire_dash": "a" * 40,
    "aspire_data": "b" * 40,
}


def _mk_app(tmp_path, name, body):
    d = tmp_path / name
    d.mkdir()
    (d / "requirements.txt").write_text(body, encoding="utf-8")
    return d / "requirements.txt"


def test_scan_classifies_ok_drift_and_branch(tmp_path):
    from aspire_data.pins import scan
    _mk_app(tmp_path, "app_ok",
            f"dash>=2.17\naspire_dash @ git+https://github.com/kennymcmillan/aspire_dash.git@{'a'*40}\n")
    _mk_app(tmp_path, "app_drift",
            f"aspire_data @ git+https://github.com/kennymcmillan/aspire_data.git@{'c'*40}\n")
    _mk_app(tmp_path, "app_branch",
            "aspire_dash @ git+https://github.com/kennymcmillan/aspire_dash.git@main\n")
    rows = {r["app"]: r["status"] for r in scan(tmp_path, CUR)}
    assert rows == {"app_ok": "ok", "app_drift": "drift", "app_branch": "branch"}


def test_scan_short_sha_prefix_counts_as_ok(tmp_path):
    from aspire_data.pins import scan
    _mk_app(tmp_path, "app_short",
            f"aspire_dash @ git+https://github.com/kennymcmillan/aspire_dash.git@{'a'*8}\n")
    (row,) = scan(tmp_path, CUR)
    assert row["status"] == "ok"


def test_bump_rewrites_only_drifted_sha(tmp_path):
    from aspire_data.pins import bump_file, scan
    req = _mk_app(tmp_path, "app", (
        f"aspire_dash @ git+https://github.com/kennymcmillan/aspire_dash.git@{'c'*40}\n"
        "aspire_data @ git+https://github.com/kennymcmillan/aspire_data.git@main\n"
        "pandas>=2.0\n"
    ))
    changed = bump_file(req, CUR)
    assert changed == ["aspire_dash"]
    text = req.read_text(encoding="utf-8")
    assert f"aspire_dash.git@{'a'*40}" in text
    assert "aspire_data.git@main" in text        # branch ref untouched
    assert "pandas>=2.0" in text
    # idempotent: second run changes nothing
    assert bump_file(req, CUR) == []
    assert all(r["status"] in ("ok", "branch") for r in scan(tmp_path, CUR))


def test_bump_leaves_current_pin_alone(tmp_path):
    from aspire_data.pins import bump_file
    req = _mk_app(tmp_path, "app",
                  f"aspire_data @ git+https://github.com/kennymcmillan/aspire_data.git@{'b'*40}\n")
    before = req.read_text(encoding="utf-8")
    assert bump_file(req, CUR) == []
    assert req.read_text(encoding="utf-8") == before


def test_scan_covers_requirements_variants(tmp_path):
    from aspire_data.pins import scan
    d = tmp_path / "app"
    d.mkdir()
    (d / "requirements-api.txt").write_text(
        f"aspire_data @ git+https://github.com/kennymcmillan/aspire_data.git@{'c'*40}\n",
        encoding="utf-8")
    (row,) = scan(tmp_path, CUR)
    assert row["status"] == "drift" and row["file"].endswith("requirements-api.txt")

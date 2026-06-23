"""benchmarks: shape raw results into the inputs for the aspire_dash
benchmarking visual (`aspire_dash.plots.percentile_age_chart`).

The data-layer companion to that chart: any sport's app calls these to wire
results into the chart without hand-rolling age, personal-best, direction, or
qualifying-standard logic. Pairs the aspire_dash visual with an aspire_data
data helper (the "component backed by aspire_data" pattern).

Typical use:

    from aspire_data.benchmarks import benchmark_inputs
    from aspire_dash.plots import percentile_age_chart

    inp = benchmark_inputs(results_df, dob="2008-04-14", sex="Male", event="800m")
    fig = percentile_age_chart(
        marks=inp["marks"], reference_lines=inp["reference_lines"],
        lower_is_better=inp["lower_is_better"], value_format=inp["value_format"],
        y_title="800m", title="800m progression vs age")
"""
from __future__ import annotations

import datetime as _dt
import logging
import re
from functools import lru_cache

logger = logging.getLogger(__name__)

# Field events: higher mark is better, value is a distance in metres.
_FIELD_KEYS = ("Jump", "Vault", "Throw", "Put", "Discus", "Hammer", "Javelin")


def _records(data) -> list[dict]:
    """DataFrame or list-of-dicts -> list of dicts (no hard pandas dependency)."""
    if data is None:
        return []
    if hasattr(data, "to_dict"):           # pandas DataFrame
        return data.to_dict("records")
    return [dict(r) for r in data]


def _to_date(v):
    if v is None or v == "":
        return None
    if isinstance(v, _dt.datetime):
        return v.date()
    if isinstance(v, _dt.date):
        return v
    try:
        return _dt.date.fromisoformat(str(v)[:10])
    except ValueError:
        return None


def _num(v):
    try:
        return float(v)
    except (TypeError, ValueError):
        return None


def event_direction(event) -> tuple[bool, str | None]:
    """(lower_is_better, value_format) inferred from an athletics event name.
    Track runs and race walks: faster (lower) is better, formatted as time.
    Field events and combined events: higher is better, plain number."""
    e = str(event or "")
    if any(k in e for k in _FIELD_KEYS) or "athlon" in e.lower():
        return False, None
    return True, "time"


def marks_from_results(results, dob, *, date_col="Start_Date",
                       value_col="Result_numerical", event=None,
                       event_col="Event_standard", lower_is_better=None,
                       age_range=(8, 40)) -> list[dict]:
    """Raw results -> ``[{age, mark, pb}]`` for ``percentile_age_chart(marks=...)``.

    Computes decimal age-at-result from ``dob`` and flags running-best personal
    bests (direction from ``lower_is_better``, inferred from ``event`` if None).
    ``results`` is a DataFrame or list of dicts; ``dob`` an ISO string or date.
    Pass ``event`` to filter ``results`` to one event via ``event_col``.
    """
    born = _to_date(dob)
    if born is None:
        return []
    if lower_is_better is None:
        lower_is_better = event_direction(event)[0]

    rows = _records(results)
    if event is not None and event_col:
        rows = [r for r in rows if str(r.get(event_col)) == str(event)]

    pts = []
    for r in rows:
        d = _to_date(r.get(date_col))
        v = _num(r.get(value_col))
        if d is None or v is None:
            continue
        age = (d - born).days / 365.25
        if not (age_range[0] <= age <= age_range[1]):
            continue
        pts.append((d, age, v))
    pts.sort(key=lambda t: t[0])

    best, out = None, []
    for _d, age, v in pts:
        is_pb = best is None or (v < best if lower_is_better else v > best)
        if is_pb:
            best = v
        out.append({"age": round(age, 2), "mark": v, "pb": is_pb})
    return out


@lru_cache(maxsize=4)
def _standards(pin: str):
    import pandas as pd
    try:
        from .pinboard import read_pin
        df = read_pin(pin)
        return df if isinstance(df, pd.DataFrame) else pd.DataFrame()
    except Exception as e:  # pin missing / no access / offline
        logger.info("standards pin %s unavailable: %s", pin, e)
        return pd.DataFrame()


def _sex_code(sex) -> str:
    s = str(sex or "").strip().lower()
    return "W" if s in ("female", "f", "w", "women") else "M"


def standard_line(event, sex, *, pin="world_athletics_u20_standards",
                  label="World U20 standard", color=None) -> dict | None:
    """Resolve a qualifying-standard reference line from a pinned standards table
    (columns: event_standard, sex [M/W], standard_numeric, standard_raw, metric,
    lower_is_better). Returns a dict ready for ``reference_lines`` plus the
    direction/metric, or None when there is no standard."""
    df = _standards(pin)
    if df.empty or not event or "event_standard" not in df.columns:
        return None
    import pandas as pd
    m = df[(df["event_standard"].astype(str) == str(event))
           & (df["sex"].astype(str) == _sex_code(sex))]
    if m.empty:
        return None
    r = m.iloc[0]
    num = r.get("standard_numeric")
    if num is None or (isinstance(num, float) and pd.isna(num)):
        return None
    raw = r.get("standard_raw")
    out = {"y": float(num), "label": f"{label} ({raw})" if raw else label,
           "metric": r.get("metric"), "lower_is_better": bool(r.get("lower_is_better"))}
    if color:
        out["color"] = color
    return out


# --- Historical percentile norms (Oracle aspire_data_event_percentiles) --------
# EVENT x AGE_BIN, mark at every 5% (p0=worst .. p100=best) from the Power-of-10
# PERCENTILE_RANK norms. These ARE the percentile bands for percentile_age_chart;
# never compute bands from a small squad. Promoted from development_dashboard's
# lib/percentiles.py so every app and both libraries share one source.
_NORM_TABLE = "aspire_data_event_percentiles"

# canonical Event -> norm base event (None => no international norm)
_EVENT_BASE = {
    "60m": "60m", "100m": "100m", "200m": "200m", "300m": "300m", "400m": "400m",
    "800m": "800m", "1000m": "1000m", "1500m": "1500m", "2000m": "2000m",
    "3000m": "3000m", "5000m": "5000m", "10000m": "10000m",
    "60m Hurdles": "60mH", "110m Hurdles": "110mH",
    "Long Jump": "Long Jump", "High Jump": "High Jump",
    "Triple Jump": "Triple Jump", "Pole Vault": "Pole Vault",
    "Shot Put": "Shot Put", "Discus Throw": "Discus Throw",
    "Hammer Throw": "Hammer Throw", "Javelin Throw": "Javelin Throw",
}

# implement weight / hurdle height by age (BOYS) -> norm-event variant suffix.
# (max_age_exclusive, suffix); final (999, ...) is the senior implement.
_IMPLEMENT_BY_AGE = {
    "Shot Put":      [(14, "(3kg)"), (16, "(4kg)"), (18, "(5kg)"), (20, "(6kg)"), (999, "")],
    "Discus Throw":  [(12, "(0.750kg)"), (14, "(1.000kg)"), (16, "(1.250kg)"),
                      (18, "(1.500kg)"), (20, "(1.750kg)"), (999, "")],
    "Hammer Throw":  [(14, "(3kg)"), (16, "(4kg)"), (18, "(5kg)"), (20, "(6kg)"), (999, "")],
    "Javelin Throw": [(12, "(400g)"), (14, "(500g)"), (16, "(600g)"),
                      (18, "(700g)"), (999, "(800g)")],
    "110mH":         [(16, "(84cm)"), (18, "(91cm)"), (20, "(99cm)"), (999, "")],
}

_NORMS_DF = None   # process cache for the default-query path (static reference)


def _norm_variant(base, age):
    table = _IMPLEMENT_BY_AGE.get(base)
    if not table or age is None:
        return base
    for max_age, suffix in table:
        if age < max_age:
            return f"{base} {suffix}".strip()
    return base


def map_norm_event(event, age=None):
    """Canonical Event (+ age for implement / hurdle-height events) -> the norm
    EVENT name in the percentile table, or None when no international norm
    exists (relays, Cricket Ball Throw, Pentathlon, ...)."""
    base = _EVENT_BASE.get(str(event).strip())
    if base is None:
        return None
    return _norm_variant(base, age)


def _bin_centre(age_bin):
    m = re.findall(r"[\d.]+", str(age_bin))
    if len(m) == 2:
        return (float(m[0]) + float(m[1])) / 2
    if len(m) == 1:
        return float(m[0])
    return None


def _default_norms_query(table_name, where_clause, limit):
    from .sports_api import SportsApi
    return SportsApi().tool("query_table", table_name=table_name,
                            where_clause=where_clause, limit=limit)


def percentile_norms(query=None):
    """The Oracle percentile-norms table as a DataFrame (event, age_bin, p0..p100).

    ``query(table_name, where_clause, limit) -> rows`` defaults to aspire_data's
    SportsApi (reads ``SPORTS_API_URL``); pass your own app's client to reuse its
    caching/auth. The default-query result is process-cached (static reference).
    Empty DataFrame on any failure (fail-soft).
    """
    global _NORMS_DF
    if query is None and _NORMS_DF is not None:
        return _NORMS_DF
    import pandas as pd
    q = query or _default_norms_query
    try:
        rows = q(_NORM_TABLE, "1=1", 20000)
    except Exception as e:
        logger.info("percentile norms unavailable: %s", e)
        return pd.DataFrame()
    if isinstance(rows, dict):
        rows = (rows.get("result", {}).get("data", {}).get("records")
                or rows.get("records") or [])
    df = pd.DataFrame(rows or [])
    if not df.empty:
        df.columns = [str(c).lower() for c in df.columns]
        df["event"] = df["event"].astype(str)
    if query is None:
        _NORMS_DF = df
    return df


def standard_bands(event, *, age=None, pct=(10, 25, 50, 75, 90), elite=100,
                   query=None, norms=None):
    """Percentile-band DataFrame for ``percentile_age_chart(bands=...)`` straight
    from the historical Oracle norms: one row per age (the age-bin centre), with
    columns ``age`` + ``p{n}`` for each n in ``pct`` (plus ``p{elite}`` when
    ``elite`` is set, for the chart's elite ceiling line). The mark at ``p90`` is
    better than 90% of the population, so it pairs with ``lower_is_better`` on the
    chart for both run (faster) and field (farther) events.

    For implement / hurdle-height events the correct variant is chosen per age
    bin. Empty DataFrame when the event has no norm. Reads via ``query`` /
    aspire_data SportsApi unless a pre-fetched ``norms`` df is supplied.
    """
    import pandas as pd
    base = _EVENT_BASE.get(str(event).strip())
    if base is None:
        return pd.DataFrame()
    df = norms if norms is not None else percentile_norms(query)
    if df is None or df.empty or "event" not in df.columns:
        return pd.DataFrame()

    cols = list(dict.fromkeys(list(pct) + ([elite] if elite is not None else [])))
    impl = _IMPLEMENT_BY_AGE.get(base)
    wanted = ({f"{base} {suf}".strip() for _, suf in impl} | {base}) if impl else {base}
    fam = df[df["event"].isin(wanted)].copy()
    if fam.empty:
        return pd.DataFrame()
    fam["age"] = fam["age_bin"].map(_bin_centre)
    fam = fam.dropna(subset=["age"])
    if fam.empty:
        return pd.DataFrame()

    out_rows = []
    for a, grp in fam.groupby("age"):
        if impl:                                   # pick the age-correct implement
            pick = grp[grp["event"] == _norm_variant(base, a)]
            if pick.empty:
                pick = grp[grp["event"] == base]
            if pick.empty:
                continue
            r = pick.iloc[0]
        else:
            r = grp.iloc[0]
        row = {"age": float(a)}
        for p in cols:
            col = f"p{p}"
            row[col] = pd.to_numeric(r.get(col), errors="coerce") if col in grp.columns else None
        out_rows.append(row)

    out = pd.DataFrame(out_rows)
    if out.empty:
        return out
    value_cols = [c for c in out.columns if c != "age"]
    out = out.dropna(how="all", subset=value_cols)
    return out.sort_values("age").reset_index(drop=True)


def percentile_of_mark(event, mark, *, age=None, query=None, norms=None):
    """Inverse of :func:`standard_bands`: where does ``mark`` fall within the
    historical Power-of-10 percentile norms for this event at this ``age``?

    Returns a percentile 0..100 (float, "this mark is better than ~P% of the
    norm population"), or ``None`` when the event has no international norm or
    the norms are unavailable (fail-soft). Use it for a single percentile KPI
    or to band one competition result, sharing one source with the chart bands.

    Interpolates linearly across every ``p`` column present (p0..p100, not just
    the five chart bands) of the age band whose centre is nearest ``age``; marks
    beyond the best/worst column clamp to 100/0. Direction is implicit in the
    norms (the p100 column is always the best mark, faster OR farther), so this
    works for both run and field events without a direction flag. For
    implement / hurdle-height events the age-correct variant is chosen.

    ``age`` is effectively required when an event has more than one age band.
    Reads via ``query`` / aspire_data SportsApi unless a pre-fetched ``norms``
    DataFrame is supplied.
    """
    import pandas as pd
    m = _num(mark)
    if m is None:
        return None
    base = _EVENT_BASE.get(str(event).strip())
    if base is None:
        return None
    df = norms if norms is not None else percentile_norms(query)
    if df is None or df.empty or "event" not in df.columns:
        return None

    impl = _IMPLEMENT_BY_AGE.get(base)
    wanted = ({f"{base} {suf}".strip() for _, suf in impl} | {base}) if impl else {base}
    fam = df[df["event"].isin(wanted)].copy()
    if fam.empty:
        return None
    fam["age_centre"] = fam["age_bin"].map(_bin_centre)
    fam = fam.dropna(subset=["age_centre"])
    if fam.empty:
        return None

    # pick the age band nearest `age`; the age-correct implement variant breaks ties
    if age is not None:
        fam = fam.assign(_d=(fam["age_centre"] - float(age)).abs()).sort_values("_d")
        if impl:
            variant = _norm_variant(base, age)
            pick = fam[fam["event"] == variant]
            row = (pick if not pick.empty else fam).iloc[0]
        else:
            row = fam.iloc[0]
    elif len(fam) == 1:
        row = fam.iloc[0]
    else:
        return None   # ambiguous without an age

    # pair each percentile column with its mark, then read the curve at `mark`
    pairs = []
    for col in row.index:
        c = str(col)
        if c.startswith("p") and c[1:].isdigit():
            v = _num(row[col])
            if v is not None:
                pairs.append((float(c[1:]), v))
    if len(pairs) < 2:
        return None
    pairs.sort(key=lambda t: t[1])               # by mark ascending
    marks, pcts = [], []
    for p, mk in pairs:                          # drop duplicate marks (keep first)
        if not marks or mk > marks[-1]:
            marks.append(mk)
            pcts.append(p)
    if len(marks) < 2:
        return round(pcts[0], 1)
    if m <= marks[0]:
        return round(pcts[0], 1)
    if m >= marks[-1]:
        return round(pcts[-1], 1)
    for i in range(1, len(marks)):
        if m <= marks[i]:
            x0, x1, y0, y1 = marks[i - 1], marks[i], pcts[i - 1], pcts[i]
            return round(y0 + (y1 - y0) * (m - x0) / (x1 - x0), 1)
    return round(pcts[-1], 1)


def age_band_centre(age):
    """The Power-of-10 age-band centre for a decimal age: the integer year, with
    the lower edge inclusive at ``N-0.5`` and the upper exclusive at ``N+0.5``
    (so 12.5..13.499 -> 13, 13.5 -> 14). Matches the integer-centred bands stored
    in ``aspire_data_event_percentiles`` ('12.5 - 13.5', '13.5 - 14.5', ...), so a
    band centre feeds straight into :func:`percentile_of_mark` as ``age=``.
    Returns ``None`` for a non-numeric age."""
    a = _num(age)
    if a is None:
        return None
    return float(int(a + 0.5))


def best_pb_by_ageband(results, dob, *, event=None, date_col="Start_Date",
                       value_col="Result_numerical", event_col="Event_standard",
                       lower_is_better=None, age_range=(8, 40),
                       with_percentile=False, norms=None, query=None) -> list[dict]:
    """Best mark in each Power-of-10 age band for one athlete and one event.

    Buckets ``results`` into the integer-year bands of
    :func:`age_band_centre` (best = fastest for track / walks, farthest for
    field; direction inferred from ``event`` unless ``lower_is_better`` is set),
    and returns one row per band the athlete competed in, ascending::

        [{age_band, age, mark, date, n}, ...]

    ``age_band`` is the integer band centre, ``age`` the decimal age at which the
    band-best mark was set, ``n`` the number of results in the band. With
    ``with_percentile=True`` (needs ``event``) each row also gets ``percentile``
    via :func:`percentile_of_mark` at the band centre, against the SAME historical
    norms - i.e. a percentile-per-age-band series, the input shape for trajectory
    modelling. ``results`` is a DataFrame or list of dicts; ``dob`` ISO/date.
    """
    born = _to_date(dob)
    if born is None:
        return []
    if lower_is_better is None:
        lower_is_better = event_direction(event)[0]

    rows = _records(results)
    if event is not None and event_col:
        rows = [r for r in rows if str(r.get(event_col)) == str(event)]

    buckets: dict[float, dict] = {}
    for r in rows:
        d = _to_date(r.get(date_col))
        v = _num(r.get(value_col))
        if d is None or v is None:
            continue
        age = (d - born).days / 365.25
        if not (age_range[0] <= age <= age_range[1]):
            continue
        band = age_band_centre(age)
        if band is None:
            continue
        b = buckets.setdefault(band, {"age_band": band, "age": None,
                                      "mark": None, "date": None, "n": 0})
        b["n"] += 1
        if b["mark"] is None or (v < b["mark"] if lower_is_better else v > b["mark"]):
            b["mark"], b["age"], b["date"] = v, round(age, 2), d.isoformat()

    out = [buckets[k] for k in sorted(buckets)]
    if with_percentile and event is not None:
        for row in out:
            row["percentile"] = percentile_of_mark(
                event, row["mark"], age=row["age_band"], norms=norms, query=query)
    return out


def benchmark_inputs(results, dob, sex, event, *,
                     pin="world_athletics_u20_standards",
                     date_col="Start_Date", value_col="Result_numerical",
                     event_col="Event_standard", extra_refs=None,
                     pct=(10, 25, 50, 75, 90), bands=True,
                     norms_query=None) -> dict:
    """One call -> everything ``percentile_age_chart`` needs for one athlete and
    one event: ``{marks, reference_lines, bands, pct, lower_is_better,
    value_format}``.

    Resolves direction and unit format from the qualifying standard when present
    (else infers from the event name), shapes the marks, builds the standard
    reference line, and pulls the percentile bands from the historical Oracle
    norms (``aspire_data_event_percentiles``) via :func:`standard_bands`. Pass
    ``bands=False`` to skip the norms lookup, or ``norms_query`` to reuse your
    app's Sports API client. ``extra_refs`` (records, etc.) are appended to
    ``reference_lines``.
    """
    std = standard_line(event, sex, pin=pin)
    if std:
        lower = std["lower_is_better"]
        vfmt = "time" if std.get("metric") == "time" else None
    else:
        lower, vfmt = event_direction(event)

    marks = marks_from_results(results, dob, date_col=date_col, value_col=value_col,
                               event=event, event_col=event_col, lower_is_better=lower)
    refs = []
    if std:
        refs.append({"y": std["y"], "label": std["label"], **(
            {"color": std["color"]} if "color" in std else {})})
    if extra_refs:
        refs.extend(extra_refs)

    band_df = None
    if bands:
        try:
            band_df = standard_bands(event, pct=pct, query=norms_query)
            if band_df is not None and getattr(band_df, "empty", True):
                band_df = None
        except Exception as e:  # norms unavailable / offline
            logger.info("standard_bands failed for %s: %s", event, e)
            band_df = None

    return {"marks": marks, "reference_lines": refs, "bands": band_df,
            "pct": tuple(pct), "lower_is_better": lower, "value_format": vfmt}

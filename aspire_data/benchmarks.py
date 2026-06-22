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


def benchmark_inputs(results, dob, sex, event, *,
                     pin="world_athletics_u20_standards",
                     date_col="Start_Date", value_col="Result_numerical",
                     event_col="Event_standard", extra_refs=None) -> dict:
    """One call -> everything ``percentile_age_chart`` needs for one athlete and
    one event: ``{marks, reference_lines, lower_is_better, value_format}``.

    Resolves direction and unit format from the qualifying standard when present
    (else infers from the event name), shapes the marks, and builds the standard
    reference line. ``extra_refs`` (records, etc.) are appended to
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
    return {"marks": marks, "reference_lines": refs,
            "lower_is_better": lower, "value_format": vfmt}

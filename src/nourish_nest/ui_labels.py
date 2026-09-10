"""Presentation-only labels; identity stays in API payloads and session state."""

import re
from collections import Counter

import streamlit as st


def humanize(value) -> str:
    text = str(value)
    return {"development_fixture": "Development sample", "usda": "USDA"}.get(
        text, text.replace("_", " ").replace("-", " ").capitalize()
    )


def labels(rows, name=lambda r: r.name, context=lambda r: "") -> dict:
    rows = list(rows)
    names = Counter(name(r).casefold() for r in rows)
    base = [name(r) + (f" — {context(r)}" if context(r) else "") for r in rows]
    counts = Counter(base)
    seen = Counter()
    result = {}
    for row, label in zip(rows, base, strict=True):
        seen[label] += 1
        # An ordinal is the fallback when even meaningful context is identical.
        suffix = f" (option {seen[label]})" if counts[label] > 1 else ""
        result[row.id] = (
            label if names[name(row).casefold()] > 1 or context(row) else name(row)
        ) + suffix
    return result


def food_labels(rows):
    return labels(rows, context=lambda r: r.brand or humanize(r.source_provider or r.source_type))


def technical_details(**values):
    with st.expander("Technical details", expanded=False):
        for label, value in values.items():
            if value is not None:
                st.text(f"{humanize(label).replace(' id', ' ID')}: {value}")


def friendly_message(message):
    return re.sub(
        r"\b[0-9a-fA-F]{8}(?:-[0-9a-fA-F]{4}){3}-[0-9a-fA-F]{12}\b", "the selected record", message
    )


def unit_label(unit):
    names = {
        "g": "grams",
        "kg": "kilograms",
        "oz": "ounces",
        "lb": "pounds",
        "ml": "millilitres",
        "l": "litres",
        "tbsp": "tablespoons",
        "tsp": "teaspoons",
        "cup": "cups",
        "item": "items",
    }
    return f"{unit} — {names[unit]}" if unit in names else unit


def reset_fields(prefix):
    for key in list(st.session_state):
        if key.startswith(prefix + "_"):
            del st.session_state[key]


def validation_errors(error):
    st.error("Please check the highlighted details before saving. Your entries have been kept.")
    for issue in error.errors():
        field = " / ".join(humanize(part) for part in issue["loc"]) or "Details"
        st.warning(f"{field}: {friendly_message(issue['msg'])}")

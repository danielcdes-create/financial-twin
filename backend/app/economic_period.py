"""Declared economic competence; cash always keeps the original event instant."""
import re
from datetime import date

from app.financial_time import date_filter, day_start, local_date


def economic_month(item):
    declared = item.get("competencia")
    if declared not in (None, ""):
        if not isinstance(declared, str) or not re.fullmatch(r"\d{4}-(0[1-9]|1[0-2])", declared): return None
        try: date.fromisoformat(declared + "-01")
        except ValueError: return None
        return declared
    return local_date(item["data_evento"]).strftime("%Y-%m") if item.get("data_evento") else None


def economic_basis(item):
    return "competencia_declarada" if item.get("competencia") else "data_evento_brasilia"


def economic_in_period(item, start, end):
    if item.get("competencia"):
        month = economic_month(item)
        return bool(month and start.strftime("%Y-%m") <= month <= end.strftime("%Y-%m"))
    return bool(item.get("data_evento") and start <= local_date(item["data_evento"]) <= end)


def economic_instant(item):
    month = economic_month(item)
    if item.get("competencia"):
        return day_start(date.fromisoformat(month + "-01")) if month else None
    return item.get("data_evento")


def economic_rows(collection, start, end, query=None):
    """Use indexed competence and event windows, then deduplicate their union."""
    query = query or {}
    declared = collection.find({**query, "competencia": {"$gte": start.strftime("%Y-%m"), "$lte": end.strftime("%Y-%m")}})
    dated = collection.find({**query, "data_evento": date_filter(start, end)})
    rows = {str(row.get("_id")): row for row in [*declared, *dated] if economic_in_period(row, start, end)}
    return list(rows.values())

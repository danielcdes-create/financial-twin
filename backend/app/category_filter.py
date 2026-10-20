"""Use the same uncategorized bucket in summaries and movement filters."""
from app.atomic import with_batch_size

UNCATEGORIZED = "Sem categoria"
ACTIVE_CAPTURES = ["provisional", "enriched", "confirmed"]


def _has_collection(db, name: str) -> bool:
    return not isinstance(db, dict) or name in db


def catalog_category_names(
    db,
    *,
    all_categories=None,
    commitments_rows=None,
    overrides_rows=None,
    experiments_rows=None,
    tx_categories=None,
    prov_categories=None,
):
    """Live choices include persisted ledger, active capture, commitment and experiment classifications."""
    if tx_categories is not None:
        names = [str(value) for value in tx_categories if value]
    else:
        names = [str(value) for value in db.transactions.distinct("categoria") if value]
    if prov_categories is not None:
        names += [str(value) for value in prov_categories if value]
    else:
        names += [str(value) for value in db.provisional_transactions.distinct(
            "categoria_sugerida", {"status": {"$in": ACTIVE_CAPTURES}}) if value]
    if commitments_rows is not None or _has_collection(db, "commitments"):
        rows = commitments_rows if commitments_rows is not None else with_batch_size(db.commitments.find({}))
        for row in rows:
            cat = row.get("category") or row.get("categoria")
            if cat and str(cat).strip():
                names.append(str(cat).strip())
    if overrides_rows is not None or _has_collection(db, "future_schedule_overrides"):
        rows = overrides_rows if overrides_rows is not None else with_batch_size(db.future_schedule_overrides.find({}))
        for row in rows:
            profile = row.get("profile") if isinstance(row.get("profile"), dict) else {}
            cat = profile.get("categoria")
            if cat and str(cat).strip():
                names.append(str(cat).strip())
    if experiments_rows is not None or _has_collection(db, "saving_experiments"):
        rows = experiments_rows if experiments_rows is not None else with_batch_size(db.saving_experiments.find({}))
        for row in rows:
            cat = row.get("category") or row.get("categoria")
            if cat and str(cat).strip():
                names.append(str(cat).strip())
    all_cats = list(all_categories) if all_categories is not None else list(with_batch_size(db.categories.find({})))
    active_categories = [item for item in all_cats if item.get("active") is not False and item.get("name")]
    names += [item["name"] for item in active_categories]
    inactive = {str(item.get("name") or "").strip().casefold() for item in all_cats if item.get("active") is False and item.get("name")}
    if inactive:
        active_explicit = {str(item.get("name") or "").strip().casefold() for item in active_categories}
        retired = inactive - active_explicit
        if retired:
            names = [name for name in names if name.strip().casefold() not in retired]
    return names


def display_category(value):
    return value or UNCATEGORIZED


def category_query(field, category):
    selected = (category or "").strip()
    if not selected:
        return {}
    # MongoDB's null match includes an absent field. Keep explicit historical
    # labels too, without writing a synthetic category into financial records.
    return {field: {"$in": [None, "", UNCATEGORIZED]} if selected == UNCATEGORIZED else selected}


def matches_category(value, category):
    selected = (category or "").strip()
    return not selected or display_category(value) == selected

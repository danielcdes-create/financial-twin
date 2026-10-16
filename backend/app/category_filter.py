"""Use the same uncategorized bucket in summaries and movement filters."""

UNCATEGORIZED = "Sem categoria"
ACTIVE_CAPTURES = ["provisional", "enriched", "confirmed"]


def _has_collection(db, name: str) -> bool:
    return not isinstance(db, dict) or name in db


def catalog_category_names(db):
    """Live choices include persisted ledger, active capture, commitment and experiment classifications."""
    names = [str(value) for value in db.transactions.distinct("categoria") if value]
    names += [str(value) for value in db.provisional_transactions.distinct(
        "categoria_sugerida", {"status": {"$in": ACTIVE_CAPTURES}}) if value]
    if _has_collection(db, "commitments"):
        for row in db.commitments.find({}):
            cat = row.get("category") or row.get("categoria") or row.get("categoria")
            if cat and str(cat).strip():
                names.append(str(cat).strip())
    if _has_collection(db, "future_schedule_overrides"):
        for row in db.future_schedule_overrides.find({}):
            profile = row.get("profile") if isinstance(row.get("profile"), dict) else {}
            cat = profile.get("categoria")
            if cat and str(cat).strip():
                names.append(str(cat).strip())
    if _has_collection(db, "saving_experiments"):
        for row in db.saving_experiments.find({}):
            cat = row.get("category") or row.get("categoria")
            if cat and str(cat).strip():
                names.append(str(cat).strip())
    names += [item["name"] for item in db.categories.find({"active": {"$ne": False}}) if item.get("name")]
    inactive = {str(item.get("name") or "").strip().casefold() for item in db.categories.find({}) if item.get("active") is False and item.get("name")}
    if inactive:
        active_explicit = {str(item.get("name") or "").strip().casefold() for item in db.categories.find({"active": {"$ne": False}}) if item.get("name")}
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

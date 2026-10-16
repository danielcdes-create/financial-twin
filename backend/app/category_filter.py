"""Use the same uncategorized bucket in summaries and movement filters."""

UNCATEGORIZED = "Sem categoria"
ACTIVE_CAPTURES = ["provisional", "enriched", "confirmed"]


def catalog_category_names(db):
    """Live choices include persisted ledger and active capture classifications."""
    names = [str(value) for value in db.transactions.distinct("categoria") if value]
    names += [str(value) for value in db.provisional_transactions.distinct(
        "categoria_sugerida", {"status": {"$in": ACTIVE_CAPTURES}}) if value]
    names += [item["name"] for item in db.categories.find({"active": {"$ne": False}}) if item.get("name")]
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

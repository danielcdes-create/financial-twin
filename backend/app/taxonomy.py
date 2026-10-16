import re
import unicodedata
from typing import Any


NATURES = (
    "COMPRA",
    "IMPOSTO_TRIBUTO",
    "TARIFA_ENCARGO",
    "ESTORNO",
    "RENDIMENTO",
    "TRANSFERENCIA",
)

NATURE_LABELS = {
    "COMPRA": "Compras e Consumo",
    "IMPOSTO_TRIBUTO": "Impostos e Tributos",
    "TARIFA_ENCARGO": "Tarifas e Encargos",
    "ESTORNO": "Estornos e Reembolsos",
    "RENDIMENTO": "Rendimentos e Receitas",
    "TRANSFERENCIA": "Transferência Neutra",
}

# Subcategorias aprovadas para os centros de custo abrangidos pela NT-FIN-001.
# As opções já existentes na base são somadas a este catálogo pela API.
CURATED_SUBCATEGORIES = {
    "Habitação": (
        "Aluguel",
        "Condomínio",
        "Contas de Consumo",
        "Manutenção",
        "IPTU",
    ),
    "Transporte": (
        "Combustível",
        "Manutenção Mecânica",
        "Seguro",
        "Estacionamento e Pedágio",
        "IPVA e Licenciamento",
    ),
    "Despesas Financeiras": (
        "IOF (Internacional / Câmbio)",
        "IOF (Operações / Fatura)",
        "IOF (Não especificado)",
        "Juros",
        "Multas",
        "Tarifas e Anuidades",
        "Taxas de Serviço",
    ),
}


def normalized(value: Any) -> str:
    text = unicodedata.normalize("NFKD", str(value or ""))
    text = text.encode("ascii", "ignore").decode("ascii").upper()
    return re.sub(r"\s+", " ", re.sub(r"[^A-Z0-9]+", " ", text)).strip()


def _infer_classification(record: dict[str, Any]) -> dict[str, str]:
    """Return only deterministic taxonomy suggestions; ambiguity stays unset."""
    description = normalized(
        record.get("descricao_original")
        or record.get("estabelecimento_original")
        or record.get("merchant_raw")
    )
    legacy_type = normalized(record.get("tipo"))
    migration_reason = normalized((record.get("classification") or {}).get("migration_reason"))

    if any(token in description for token in ("ESTORNO", "REEMBOLSO", "CANCELAMENTO", "CHARGEBACK")) or legacy_type == "ESTORNO":
        return {"natureza": "ESTORNO"}

    if "IOF" in description:
        if any(token in description for token in ("USD", "EUR", "CAMBIO", "INTERNACIONAL", "EXTERIOR", "EXT")):
            subcategory = "IOF (Internacional / Câmbio)"
        elif any(token in description for token in ("ROTATIVO", "FINANC", "FATURA", "PARCEL", "MORA", "SAQUE CREDITO")):
            subcategory = "IOF (Operações / Fatura)"
        else:
            subcategory = "IOF (Não especificado)"
        return {"categoria": "Despesas Financeiras", "subcategoria": subcategory, "natureza": "IMPOSTO_TRIBUTO"}

    if "IPTU" in description:
        return {"categoria": "Habitação", "subcategoria": "IPTU", "natureza": "IMPOSTO_TRIBUTO"}
    if "IPVA" in description or "LICENCIAMENTO" in description:
        return {"categoria": "Transporte", "subcategoria": "IPVA e Licenciamento", "natureza": "IMPOSTO_TRIBUTO"}
    if "JUROS" in description:
        return {"categoria": "Despesas Financeiras", "subcategoria": "Juros", "natureza": "TARIFA_ENCARGO"}
    if "MULTA" in description:
        return {"categoria": "Despesas Financeiras", "subcategoria": "Multas", "natureza": "TARIFA_ENCARGO"}
    if "ANUIDADE" in description:
        return {"categoria": "Despesas Financeiras", "subcategoria": "Tarifas e Anuidades", "natureza": "TARIFA_ENCARGO"}

    if legacy_type in {"IMPOSTO"}:
        return {"natureza": "IMPOSTO_TRIBUTO"}
    if legacy_type in {"TARIFA", "JUROS", "MULTA"}:
        return {"natureza": "TARIFA_ENCARGO"}
    if legacy_type in {"RENDIMENTO", "RECEITA"}:
        return {"natureza": "RENDIMENTO"}
    if migration_reason == "TRANSFERENCIA PROPRIA":
        return {"natureza": "TRANSFERENCIA"}
    if legacy_type in {"COMPRA", "SEGURO"}:
        return {"natureza": "COMPRA"}
    return {}


def taxonomy_overrides(db, **kwargs) -> dict[str, dict[str, dict]]:
    return {
        normalized(category["name"]): {normalized(item["name"]): item for item in category.get("subcategories", [])}
        for category in db.categories.find({"active": {"$ne": False}}, **kwargs)
        if category.get("name")
    }


def infer_classification(record: dict[str, Any], overrides=None) -> dict[str, str]:
    suggestion = _infer_classification(record)
    name = suggestion.get("subcategoria")
    rules = (overrides or {}).get(normalized(suggestion.get("categoria")), {})
    visited = set()
    while name and normalized(name) in rules:
        key = normalized(name)
        if key in visited:
            name = None
            break
        visited.add(key)
        rule = rules[key]
        if rule.get("active", True):
            name = rule["name"]
            break
        name = rule.get("replacement")
    if name:
        suggestion["subcategoria"] = name
    else:
        suggestion.pop("subcategoria", None)
    return suggestion


def subcategory_catalog(db) -> dict[str, dict[str, dict]]:
    """Merge defaults, live usage and user overrides without resurrecting deletions."""
    values: dict[str, dict[str, dict]] = {}
    category_names = {}

    def add(category, name, source):
        category_key = normalized(category)
        category_names.setdefault(category_key, category)
        return values.setdefault(category_key, {}).setdefault(normalized(name), {
            "name": name, "transaction_count": 0, "provisional_count": 0,
            "custom": source == "custom",
        })

    for category, items in CURATED_SUBCATEGORIES.items():
        for name in items:
            add(category, name, "default")
    for collection, category_field in ((db.transactions, "categoria"), (db.provisional_transactions, "categoria_sugerida")):
        query = {category_field: {"$nin": [None, ""]}, "subcategoria": {"$nin": [None, ""]}}
        if category_field == "categoria_sugerida":
            query["status"] = {"$in": ["provisional", "enriched", "confirmed"]}
        for row in collection.find(
            query,
            {category_field: 1, "subcategoria": 1},
        ):
            category = str(row.get(category_field) or "").strip()
            subcategory = str(row.get("subcategoria") or "").strip()
            if category and subcategory:
                item = add(category, subcategory, "usage")
                item["transaction_count" if category_field == "categoria" else "provisional_count"] += 1
    # Keep the catalog in the existing categories collection (no new collection).
    for category in db.categories.find({"active": {"$ne": False}}):
        name = category.get("name")
        if not name:
            continue
        category_key = normalized(name)
        category_names[category_key] = name
        for override in category.get("subcategories", []):
            if not override.get("active", True):
                values.setdefault(category_key, {}).pop(normalized(override["name"]), None)
            else:
                item = add(name, override["name"], "custom")
                item.update(name=override["name"], custom=True)
    return {
        category_names[key]: {
            subkey: {**item, "usage_count": item["transaction_count"] + item["provisional_count"]}
            for subkey, item in items.items()
        }
        for key, items in values.items()
    }


def category_subcategories(db) -> dict[str, list[str]]:
    return {
        category: sorted((item["name"] for item in items.values()), key=str.casefold)
        for category, items in sorted(subcategory_catalog(db).items(), key=lambda pair: pair[0].casefold())
    }

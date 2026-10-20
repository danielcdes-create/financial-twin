"""NT-ARCH-MONGO-002 §07 taxonomy and the explicit label conversion.

The tree is two levels. A stored pair maps to one level-2 leaf, stays
uncategorized, or stays unresolved. Nothing here reads a merchant or a description.
"""
import re
import unicodedata
from datetime import datetime, timezone
from typing import Any

from pymongo.database import Database

from app.atomic import mongo_transaction, session_database, with_batch_size
from app.reference_cache import subcategory_catalog_cache


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

PRIOR_FIELD = "taxonomy_previous"
UNCATEGORIZED_LABEL = "Sem categoria"
PRIOR_SCHEMA = {
    "bsonType": "object",
    "additionalProperties": False,
    "required": ["categoria"],
    "properties": {
        "categoria": {"bsonType": "string", "minLength": 1},
        "subcategoria": {"bsonType": ["string", "null"]},
    },
}

# name, nature (FIXED/VARIABLE/F/V or None), budget override (None inherits the group)
_TREE = (
    {
        "name": "Habitação", "slug": "habitacao", "type": "EXPENSE", "bucket": "NEEDS",
        "mcc": ("4900", "4814", "6513", "5211"),
        "aliases": (),
        "children": (
            ("Aluguel/Prestação", "FIXED", None, "aluguel"),
            ("Condomínio", "FIXED", None, "condominio"),
            ("Energia", "VARIABLE", None, "energia"),
            ("Água/Gás", "VARIABLE", None, "agua_gas"),
            ("Internet/TV", "FIXED", None, "internet_tv"),
            ("IPTU/Seguro", "FIXED", None, "iptu_seguro"),
            ("Manutenção/Obras", "VARIABLE", None, "manutencao"),
            ("Diarista", "FIXED", None, "diarista"),
        ),
    },
    {
        "name": "Alimentação", "slug": "alimentacao", "type": "EXPENSE", "bucket": None,
        "mcc": ("5411", "5462", "5812", "5814"),
        "aliases": (),
        "children": (
            ("Supermercado/Feira/Açougue", None, "NEEDS", "supermercado"),
            ("Padaria", None, "NEEDS", "padaria"),
            ("Restaurantes/Bares", None, "WANTS", "restaurantes"),
            ("Delivery/Apps", None, "WANTS", "delivery"),
        ),
    },
    {
        "name": "Transporte", "slug": "transporte", "type": "EXPENSE", "bucket": "NEEDS",
        "mcc": ("5541", "4121", "4111", "7523", "7538"),
        "aliases": (),
        "children": (
            ("Combustível/Recarga", "VARIABLE", None, "combustivel"),
            ("Uber/99/Táxi", "VARIABLE", None, "aplicativo"),
            ("Transporte Público", "F/V", None, "publico"),
            ("Pedágio/Estacionamento", "VARIABLE", None, "pedagio"),
            ("Oficina/Peças", "VARIABLE", None, "oficina"),
            ("IPVA/Seguro", "FIXED", None, "ipva"),
        ),
    },
    {
        "name": "Saúde e Cuidados", "slug": "saude", "type": "EXPENSE", "bucket": "NEEDS",
        "mcc": ("5912", "8011", "8021", "7997", "7230"),
        "aliases": ("Saúde", "Saude e bem-estar"),
        "children": (
            ("Plano de Saúde/Odonto", "FIXED", None, "plano"),
            ("Farmácia", "VARIABLE", None, "farmacia"),
            ("Consultas/Exames/Terapia", "VARIABLE", None, "consultas"),
            ("Academia/Esportes", "FIXED", None, "academia"),
            ("Barbearia/Salão/Estética", "VARIABLE", None, "estetica"),
        ),
    },
    {
        "name": "Pets", "slug": "pets", "type": "EXPENSE", "bucket": "NEEDS",
        "mcc": ("5995", "0742"),
        "aliases": ("Pet",),
        "children": (
            ("Ração e Petiscos", "F/V", None, "racao"),
            ("Veterinário e Vacinas", "VARIABLE", None, "veterinario"),
            ("Banho e Tosa", "VARIABLE", None, "banho"),
            ("Acessórios Pet", "VARIABLE", None, "acessorios"),
        ),
    },
    {
        "name": "Lazer e Estilo", "slug": "lazer", "type": "EXPENSE", "bucket": "WANTS",
        "mcc": ("4899", "7832", "7941", "7011", "4511"),
        "aliases": ("Lazer",),
        "children": (
            ("Streaming/Assinaturas Digitais", "FIXED", None, "streaming"),
            ("Shows/Cinema/Eventos", "VARIABLE", None, "eventos"),
            ("Futebol/Sócio-Torcedor", "F/V", None, "futebol"),
            ("Viagens/Hospedagem", "VARIABLE", None, "viagens"),
            ("Hobbies", "VARIABLE", None, "hobbies"),
        ),
    },
    {
        "name": "Compras", "slug": "compras", "type": "EXPENSE", "bucket": "WANTS",
        "mcc": ("5651", "5732", "5311"),
        "aliases": (),
        "children": (
            ("Vestuário/Calçados", "VARIABLE", None, "vestuario"),
            ("Eletrônicos/Eletrodomésticos", "VARIABLE", None, "eletronicos"),
            ("Presentes e Doações", "VARIABLE", None, "presentes"),
        ),
    },
    {
        "name": "Educação", "slug": "educacao", "type": "EXPENSE", "bucket": "SAVINGS / NEEDS",
        "mcc": ("8220", "8299", "5942"),
        "aliases": (),
        "children": (
            ("Faculdade/Pós/Cursos", "FIXED", None, "faculdade"),
            ("Livros Técnicos", "VARIABLE", None, "livros"),
            ("Idiomas e Softwares", "FIXED", None, "idiomas"),
        ),
    },
    {
        "name": "Finanças/Impostos", "slug": "financas", "type": "EXPENSE", "bucket": "NEEDS / SAVINGS",
        "mcc": ("6012", "9311"),
        "aliases": (),
        "children": (
            ("Tarifas/Anuidade", "FIXED", None, "tarifas"),
            ("Juros/Multas/IOF", "VARIABLE", None, "juros"),
            ("Impostos Avulsos/IRPF", "VARIABLE", None, "impostos"),
            ("Amortização de Dívidas", "FIXED", None, "amortizacao"),
        ),
    },
    {
        "name": "Receitas", "slug": "receitas", "type": "INCOME", "bucket": None,
        "mcc": (),
        "aliases": (),
        "children": (
            ("Renda Principal", None, None, "renda_principal"),
            ("Renda Extra", None, None, "renda_extra"),
            ("Reembolsos", None, None, "reembolsos"),
        ),
        "outros": False,
    },
)

# Curated and observed labels whose text names exactly one §07 leaf.
# Absence from this table is unresolved. Description and merchant are not inputs.
_EXPLICIT = (
    ("Habitação", "Aluguel", "Habitação", "Aluguel/Prestação"),
    ("Habitação", "Manutenção", "Habitação", "Manutenção/Obras"),
    ("Habitação", "IPTU", "Habitação", "IPTU/Seguro"),
    ("Habitação", "Financiamento imobiliário", "Habitação", "Aluguel/Prestação"),
    ("Habitação", "Gás residencial", "Habitação", "Água/Gás"),
    ("Habitação", "Energia elétrica", "Habitação", "Energia"),
    ("Habitação", "Internet", "Habitação", "Internet/TV"),
    ("Transporte", "Combustível", "Transporte", "Combustível/Recarga"),
    ("Transporte", "Manutenção Mecânica", "Transporte", "Oficina/Peças"),
    ("Transporte", "Seguro", "Transporte", "IPVA/Seguro"),
    ("Transporte", "Estacionamento e Pedágio", "Transporte", "Pedágio/Estacionamento"),
    ("Transporte", "IPVA e Licenciamento", "Transporte", "IPVA/Seguro"),
    ("Transporte", "Transporte por Aplicativo", "Transporte", "Uber/99/Táxi"),
    ("Transporte", "Estacionamento", "Transporte", "Pedágio/Estacionamento"),
    ("Transporte", "Pedágio", "Transporte", "Pedágio/Estacionamento"),
    ("Despesas Financeiras", "IOF (Internacional / Câmbio)", "Finanças/Impostos", "Juros/Multas/IOF"),
    ("Despesas Financeiras", "IOF (Operações / Fatura)", "Finanças/Impostos", "Juros/Multas/IOF"),
    ("Despesas Financeiras", "IOF (Não especificado)", "Finanças/Impostos", "Juros/Multas/IOF"),
    ("Despesas Financeiras", "Juros", "Finanças/Impostos", "Juros/Multas/IOF"),
    ("Despesas Financeiras", "Multas", "Finanças/Impostos", "Juros/Multas/IOF"),
    ("Despesas Financeiras", "Tarifas e Anuidades", "Finanças/Impostos", "Tarifas/Anuidade"),
    ("Tarifas bancarias", "", "Finanças/Impostos", "Tarifas/Anuidade"),
    ("Saúde", "Academia", "Saúde e Cuidados", "Academia/Esportes"),
    ("Saúde", "farmácia", "Saúde e Cuidados", "Farmácia"),
    ("Estética", "Barbearia", "Saúde e Cuidados", "Barbearia/Salão/Estética"),
    ("Pets", "veterinária", "Pets", "Veterinário e Vacinas"),
    ("Alimentação", "supermercado", "Alimentação", "Supermercado/Feira/Açougue"),
    ("Alimentação", "hortifrúti", "Alimentação", "Supermercado/Feira/Açougue"),
    ("Alimentação", "padaria", "Alimentação", "Padaria"),
    ("Alimentação", "padaria e confeitaria", "Alimentação", "Padaria"),
    ("Alimentação", "restaurante", "Alimentação", "Restaurantes/Bares"),
    ("Alimentação", "lanchonete", "Alimentação", "Restaurantes/Bares"),
    ("Alimentação", "Fast Food", "Alimentação", "Restaurantes/Bares"),
    ("Alimentação", "hamburgueria", "Alimentação", "Restaurantes/Bares"),
    ("Alimentação", "pizzaria", "Alimentação", "Restaurantes/Bares"),
    ("Alimentação", "sorveteria", "Alimentação", "Restaurantes/Bares"),
    ("Alimentação", "gelateria", "Alimentação", "Restaurantes/Bares"),
    ("Alimentação", "marmitaria", "Alimentação", "Restaurantes/Bares"),
    ("Alimentação", "food service", "Alimentação", "Restaurantes/Bares"),
    ("Alimentação", "açaí", "Alimentação", "Restaurantes/Bares"),
    ("Alimentação", "açaí e lanches", "Alimentação", "Restaurantes/Bares"),
    ("Alimentação", "açaí e sorveteria", "Alimentação", "Restaurantes/Bares"),
    ("Alimentação", "bar e espetaria", "Alimentação", "Restaurantes/Bares"),
    ("Alimentação", "chope self-service do condomínio", "Alimentação", "Restaurantes/Bares"),
    ("Alimentação", "delivery", "Alimentação", "Delivery/Apps"),
    ("Alimentação", "pedido via iFood", "Alimentação", "Delivery/Apps"),
    ("Mercado", "", "Alimentação", "Supermercado/Feira/Açougue"),
    ("Mercado", "supermercado", "Alimentação", "Supermercado/Feira/Açougue"),
    ("Assinaturas", "Clube iFood", "Alimentação", "Delivery/Apps"),
    ("Assinaturas", "streaming", "Lazer e Estilo", "Streaming/Assinaturas Digitais"),
    ("Assinaturas", "assinaturas", "Lazer e Estilo", "Streaming/Assinaturas Digitais"),
    ("Assinaturas", "Amazon Prime", "Lazer e Estilo", "Streaming/Assinaturas Digitais"),
    ("Assinaturas", "Prime Video sem anúncios", "Lazer e Estilo", "Streaming/Assinaturas Digitais"),
    ("Assinaturas", "jornal digital", "Lazer e Estilo", "Streaming/Assinaturas Digitais"),
    ("Assinaturas", "serviço digital", "Lazer e Estilo", "Streaming/Assinaturas Digitais"),
    ("Vestuário", "vestuário", "Compras", "Vestuário/Calçados"),
    ("Vestuário", "loja de roupas", "Compras", "Vestuário/Calçados"),
    ("Outros", "eletronicos", "Compras", "Eletrônicos/Eletrodomésticos"),
    ("Outros", "Doação", "Compras", "Presentes e Doações"),
    ("Presentes", "", "Compras", "Presentes e Doações"),
    ("Turismo", "", "Lazer e Estilo", "Viagens/Hospedagem"),
    ("Receitas", "Salário", "Receitas", "Renda Principal"),
    ("Receitas", "13º", "Receitas", "Renda Principal"),
    ("Receitas", "Férias", "Receitas", "Renda Principal"),
    ("Receitas", "PLR", "Receitas", "Renda Principal"),
    ("Receitas", "Dividendos", "Receitas", "Renda Extra"),
    ("Receitas", "Freelance", "Receitas", "Renda Extra"),
    ("Receitas", "Vendas", "Receitas", "Renda Extra"),
    ("Receitas", "Corporativo", "Receitas", "Reembolsos"),
    ("Receitas", "Médico", "Receitas", "Reembolsos"),
    ("Receitas", "Cashback", "Receitas", "Reembolsos"),
)

_TARGETS = (
    {"collection": "transactions", "category_fields": ("categoria",), "nested": None},
    {"collection": "provisional_transactions", "category_fields": ("categoria_sugerida", "categoria"), "nested": None},
    {"collection": "commitments", "category_fields": ("categoria", "category"), "nested": None},
    {"collection": "future_schedule_overrides", "category_fields": ("categoria",), "nested": "profile"},
)


def normalized(value: Any) -> str:
    text = unicodedata.normalize("NFKD", str(value or ""))
    text = text.encode("ascii", "ignore").decode("ascii").upper()
    return re.sub(r"\s+", " ", re.sub(r"[^A-Z0-9]+", " ", text)).strip()


def _blank(value: Any) -> bool:
    return value is None or str(value).strip() == ""


def _text(value: Any) -> str:
    return "" if _blank(value) else str(value).strip()


def _build_tree():
    groups = {}
    leaves = {}
    aliases = {}
    catalog = []
    for group in _TREE:
        name = group["name"]
        groups[name] = group
        aliases[normalized(name)] = name
        for alias in group["aliases"]:
            aliases[normalized(alias)] = name
        prefix = "INCOME" if group["type"] == "INCOME" else "EXPENSE"
        catalog.append({
            "name": name,
            "parent": None,
            "path": f"{prefix}.{group['slug']}",
            "level": 1,
            "allow_direct_posting": False,
            "type": group["type"],
        })
        children = list(group["children"])
        if group.get("outros", True):
            children.append(("Outros", None, None, "outros"))
        leaf_index = {}
        for child_name, nature, bucket, slug in children:
            resolved_bucket = group["bucket"] if bucket is None else bucket
            node = {
                "name": child_name,
                "parent": name,
                "path": f"{prefix}.{group['slug']}.{slug}",
                "level": 2,
                "allow_direct_posting": True,
                "type": group["type"],
                "analytics": {
                    "budget_bucket": resolved_bucket,
                    "nature": nature,
                    "default_mcc_codes": list(group["mcc"]),
                },
            }
            catalog.append(node)
            leaf_index[normalized(child_name)] = child_name
        leaves[name] = leaf_index
    explicit = {}
    for category, subcategory, group_name, leaf_name in _EXPLICIT:
        explicit[(normalized(category), normalized(subcategory))] = (group_name, leaf_name)
    return groups, leaves, aliases, catalog, explicit


_GROUPS, _LEAVES, _ALIASES, _CATALOG, _EXPLICIT_PAIRS = _build_tree()

CURATED_SUBCATEGORIES = {
    group["name"]: tuple(
        node["name"] for node in _CATALOG if node["level"] == 2 and node["parent"] == group["name"]
    )
    for group in _TREE
}


def taxonomy_catalog() -> list[dict[str, Any]]:
    """The §07 tree and nothing deeper. Callers get a copy."""
    return [
        {key: (list(value) if isinstance(value, list) else (dict(value) if isinstance(value, dict) else value))
         for key, value in node.items()}
        for node in _CATALOG
    ]


def group_name(category: Any) -> str | None:
    return _ALIASES.get(normalized(category))


def classify_pair(category: Any, subcategory: Any) -> dict[str, Any]:
    """Map one stored pair. Uncategorized and unresolved are not rewritten."""
    category_text = _text(category)
    subcategory_text = _text(subcategory)
    if _blank(category_text) or normalized(category_text) == normalized(UNCATEGORIZED_LABEL):
        return {"status": "uncategorized", "categoria": category_text or None, "subcategoria": subcategory_text or None}
    group = group_name(category_text)
    leaf_key = normalized(subcategory_text)
    if group and leaf_key and leaf_key in _LEAVES[group]:
        return {"status": "destination", "categoria": group, "subcategoria": _LEAVES[group][leaf_key]}
    explicit = _EXPLICIT_PAIRS.get((normalized(category_text), leaf_key))
    if explicit:
        return {"status": "destination", "categoria": explicit[0], "subcategoria": explicit[1]}
    echo = leaf_key in {"", "OUTROS", normalized(category_text), normalized(group or "")}
    if group and echo:
        return {"status": "destination", "categoria": group, "subcategoria": "Outros"}
    return {"status": "unresolved", "categoria": category_text, "subcategoria": subcategory_text or None}


def rejects_direct_level1_posting(category: Any, subcategory: Any) -> bool:
    """A level-1 group cannot be the posting target unless a level-2 is chosen."""
    return bool(group_name(category)) and _blank(subcategory)


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
        for category in with_batch_size(db.categories.find({"active": {"$ne": False}}, **kwargs))
        if category.get("name")
    }


def infer_classification(record: dict[str, Any], overrides=None) -> dict[str, str]:
    suggestion = _infer_classification(record)
    if suggestion.get("categoria") and suggestion.get("subcategoria"):
        decision = classify_pair(suggestion["categoria"], suggestion["subcategoria"])
        if decision["status"] == "destination":
            suggestion["categoria"] = decision["categoria"]
            suggestion["subcategoria"] = decision["subcategoria"]
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


def _find_category_pairs(collection, query: dict, projection: dict):
    try:
        return with_batch_size(collection.find(query, projection))
    except TypeError:
        return with_batch_size(collection.find(query))


def subcategory_catalog(
    db,
    *,
    all_categories=None,
    preloaded_transactions=None,
    preloaded_captures=None,
) -> dict[str, dict[str, dict]]:
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
    for collection, category_field, preloaded in (
        (db.transactions, "categoria", preloaded_transactions),
        (db.provisional_transactions, "categoria_sugerida", preloaded_captures),
    ):
        count_key = "transaction_count" if category_field == "categoria" else "provisional_count"
        if preloaded is not None:
            for row in preloaded:
                if category_field == "categoria_sugerida" and row.get("status") not in {"provisional", "enriched", "confirmed"}:
                    continue
                category = str(row.get(category_field) or "").strip()
                subcategory = str(row.get("subcategoria") or "").strip()
                if category and subcategory:
                    item = add(category, subcategory, "usage")
                    item[count_key] += 1
            continue
        query = {category_field: {"$nin": [None, ""]}, "subcategoria": {"$nin": [None, ""]}}
        if category_field == "categoria_sugerida":
            query["status"] = {"$in": ["provisional", "enriched", "confirmed"]}
        for row in _find_category_pairs(collection, query, {category_field: 1, "subcategoria": 1}):
            category = str(row.get(category_field) or "").strip()
            subcategory = str(row.get("subcategoria") or "").strip()
            if category and subcategory:
                item = add(category, subcategory, "usage")
                item[count_key] += 1
    # Keep the catalog in the existing categories collection (no new collection).
    category_docs = (
        [c for c in all_categories if c.get("active") is not False]
        if all_categories is not None
        else with_batch_size(db.categories.find({"active": {"$ne": False}}))
    )
    for category in category_docs:
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


def _compute_category_subcategories(
    db,
    *,
    all_categories=None,
    preloaded_transactions=None,
    preloaded_captures=None,
) -> dict[str, list[str]]:
    return {
        category: sorted((item["name"] for item in items.values()), key=str.casefold)
        for category, items in sorted(
            subcategory_catalog(
                db,
                all_categories=all_categories,
                preloaded_transactions=preloaded_transactions,
                preloaded_captures=preloaded_captures,
            ).items(),
            key=lambda pair: pair[0].casefold(),
        )
    }


def category_subcategories(
    db,
    *,
    all_categories=None,
    preloaded_transactions=None,
    preloaded_captures=None,
) -> dict[str, list[str]]:
    raw_db = getattr(db, "db", db)
    if (
        all_categories is None
        and preloaded_transactions is None
        and preloaded_captures is None
        and isinstance(raw_db, Database)
    ):
        return subcategory_catalog_cache.get(raw_db, lambda: _compute_category_subcategories(db))
    return _compute_category_subcategories(
        db,
        all_categories=all_categories,
        preloaded_transactions=preloaded_transactions,
        preloaded_captures=preloaded_captures,
    )


def _read_pair(document: dict[str, Any], target: dict[str, Any]) -> tuple[Any, Any, list[str]]:
    if target["nested"]:
        profile = document.get(target["nested"]) if isinstance(document.get(target["nested"]), dict) else {}
        category = profile.get("categoria")
        subcategory = profile.get("subcategoria")
        return category, subcategory, ["categoria"] if not _blank(category) else []
    present = []
    category = None
    for field in target["category_fields"]:
        if field in document and not _blank(document.get(field)):
            present.append(field)
            if category is None:
                category = document.get(field)
    if category is None:
        for field in target["category_fields"]:
            if field in document:
                category = document.get(field)
                break
    return category, document.get("subcategoria"), present


def _same_pair(category: Any, subcategory: Any, decision: dict[str, Any]) -> bool:
    return _text(category) == _text(decision["categoria"]) and _text(subcategory) == _text(decision["subcategoria"])


def conversion_plan(document: dict[str, Any], target: dict[str, Any]) -> dict[str, Any]:
    category, subcategory, present = _read_pair(document, target)
    decision = classify_pair(category, subcategory)
    changed = decision["status"] == "destination" and not _same_pair(category, subcategory, decision)
    return {
        "status": "converted" if changed else decision["status"] if decision["status"] != "destination" else "unchanged",
        "decision": decision,
        "from_categoria": _text(category),
        "from_subcategoria": _text(subcategory),
        "fields": present,
        "changed": changed,
    }


def _write_plan(document: dict[str, Any], target: dict[str, Any], plan: dict[str, Any]) -> dict[str, Any]:
    decision = plan["decision"]
    previous = {
        "categoria": plan["from_categoria"] or None,
        "subcategoria": plan["from_subcategoria"] or None,
    }
    sets = {}
    if target["nested"]:
        profile = dict(document.get(target["nested"]) or {})
        profile["categoria"] = decision["categoria"]
        profile["subcategoria"] = decision["subcategoria"]
        sets[target["nested"]] = profile
    else:
        fields = plan["fields"] or [target["category_fields"][0]]
        for field in fields:
            sets[field] = decision["categoria"]
        sets["subcategoria"] = decision["subcategoria"]
    if not isinstance(document.get(PRIOR_FIELD), dict):
        sets[PRIOR_FIELD] = previous
    return sets


def _metadata(node: dict[str, Any]) -> dict[str, Any]:
    group = _GROUPS[node["name"]]
    return {
        "level": 1,
        "path": node["path"],
        "allow_direct_posting": False,
        "type": node["type"],
        "analytics": {"default_mcc_codes": list(group["mcc"])},
    }


def _store_groups(db) -> int:
    written = 0
    existing = {}
    for document in with_batch_size(db.categories.find({})):
        if document.get("name"):
            existing[normalized(document["name"])] = document
    for node in _CATALOG:
        if node["level"] != 1:
            continue
        metadata = _metadata(node)
        current = existing.get(normalized(node["name"]))
        if current and all(current.get(key) == value for key, value in metadata.items()):
            continue
        if current:
            db.categories.update_one({"_id": current["_id"]}, {"$set": metadata})
        else:
            db.categories.insert_one({
                "key": _GROUPS[node["name"]]["slug"],
                "name": node["name"],
                "active": True,
                **metadata,
            })
        written += 1
    return written


def ensure_prior_label_field(db) -> None:
    """The ledger validator rejects unknown keys. Allow the prior label on the same row."""
    if not isinstance(db, Database):
        return
    info = next(db.list_collections(filter={"name": "transactions"}), None)
    if not info:
        return
    options = info.get("options") or {}
    validator = options.get("validator") or {}
    schema = validator.get("$jsonSchema") if isinstance(validator, dict) else None
    if not isinstance(schema, dict):
        return
    properties = schema.setdefault("properties", {})
    if properties.get(PRIOR_FIELD) == PRIOR_SCHEMA:
        return
    properties[PRIOR_FIELD] = PRIOR_SCHEMA
    db.command({
        "collMod": "transactions",
        "validator": validator,
        "validationLevel": options.get("validationLevel", "strict"),
        "validationAction": options.get("validationAction", "error"),
    })


def _pair_rows(pairs: dict[tuple, int]) -> list[dict[str, Any]]:
    return [
        {
            "collection": collection, "status": status,
            "from_categoria": source_category, "from_subcategoria": source_subcategory,
            "to_categoria": destination_category, "to_subcategoria": destination_subcategory,
            "count": count,
        }
        for (collection, status, source_category, source_subcategory, destination_category, destination_subcategory), count in sorted(pairs.items())
    ]


def _audit(plan: dict[str, Any], collection: str, identity: Any, now: datetime) -> dict[str, Any]:
    return {
        "event": "category_taxonomy_converted",
        "collection": collection,
        "record_id": str(identity),
        "before": {"categoria": plan["from_categoria"] or None, "subcategoria": plan["from_subcategoria"] or None},
        "after": {"categoria": plan["decision"]["categoria"], "subcategoria": plan["decision"]["subcategoria"]},
        "created_at": now,
    }


def _scan(target_db, *, write: bool) -> dict[str, Any]:
    counts = {"converted": 0, "unchanged": 0, "unresolved": 0, "uncategorized": 0}
    pairs: dict[tuple, int] = {}
    now = datetime.now(timezone.utc)
    for target in _TARGETS:
        if not hasattr(target_db, target["collection"]):
            continue
        collection = getattr(target_db, target["collection"])
        projection = {"_id": 1, "categoria": 1, "subcategoria": 1, "category": 1, "categoria_sugerida": 1, "profile": 1, PRIOR_FIELD: 1}
        for document in list(with_batch_size(collection.find({}, projection))):
            plan = conversion_plan(document, target)
            destination = plan["status"] == "converted"
            key = (
                target["collection"], plan["status"], plan["from_categoria"], plan["from_subcategoria"],
                plan["decision"]["categoria"] if destination else plan["from_categoria"],
                plan["decision"]["subcategoria"] if destination else plan["from_subcategoria"],
            )
            pairs[key] = pairs.get(key, 0) + 1
            counts[plan["status"]] += 1
            if write and plan["changed"]:
                collection.update_one({"_id": document["_id"]}, {"$set": _write_plan(document, target, plan)})
                target_db.audit_log.insert_one(_audit(plan, target["collection"], document.get("_id"), now))
    counts["category_documents_written"] = _store_groups(target_db) if write else 0
    counts["pairs"] = _pair_rows(pairs)
    return counts


def _commit_live(db, jobs: list[tuple[str, Any]]) -> None:
    """Classification and its audit share a transaction. Batches stay inside the server limit."""
    now = datetime.now(timezone.utc)
    for start in range(0, len(jobs), 40):
        chunk = jobs[start:start + 40]
        with mongo_transaction(db) as session:
            wrapped = session_database(db, session)
            for collection_name, identity in chunk:
                target = next(item for item in _TARGETS if item["collection"] == collection_name)
                document = getattr(wrapped, collection_name).find_one({"_id": identity})
                if document is None:
                    continue
                plan = conversion_plan(document, target)
                if not plan["changed"]:
                    continue
                getattr(wrapped, collection_name).update_one({"_id": identity}, {"$set": _write_plan(document, target, plan)})
                wrapped.audit_log.insert_one(_audit(plan, collection_name, identity, now))


def apply_taxonomy_conversion(db, *, dry_run: bool = False) -> dict[str, Any]:
    """Rewrite mapped labels. Unresolved and uncategorized rows are left as stored."""
    if dry_run or not isinstance(db, Database):
        return _scan(db, write=not dry_run)
    ensure_prior_label_field(db)
    report = _scan(db, write=False)
    jobs = []
    for target in _TARGETS:
        collection = getattr(db, target["collection"])
        for document in with_batch_size(collection.find({}, {"_id": 1, "categoria": 1, "subcategoria": 1, "category": 1, "categoria_sugerida": 1, "profile": 1, PRIOR_FIELD: 1})):
            if conversion_plan(document, target)["changed"]:
                jobs.append((target["collection"], document["_id"]))
    _commit_live(db, jobs)
    with mongo_transaction(db) as session:
        report["category_documents_written"] = _store_groups(session_database(db, session))
    return report


def format_conversion_report(report: dict[str, Any]) -> str:
    """Names and counts only. Amounts and descriptions never belong here."""
    lines = [
        f"converted={report['converted']}",
        f"unchanged={report['unchanged']}",
        f"unresolved={report['unresolved']}",
        f"uncategorized={report['uncategorized']}",
        f"category_documents_written={report['category_documents_written']}",
    ]
    for pair in report["pairs"]:
        lines.append(
            "\t".join((
                pair["collection"], str(pair["count"]), pair["status"],
                pair["from_categoria"], pair["from_subcategoria"],
                pair["to_categoria"], pair["to_subcategoria"] or "",
            ))
        )
    return "\n".join(lines)

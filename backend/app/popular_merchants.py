"""Popular Brazilian merchant/franchise mapping and bank statement date helpers.

Maps corporate/acquirer descriptors (e.g. Zamp S.A., Pimenta Verde Alimentos,
Cappta, PayPal Google prefixes, utility/condominium billing codes) to popular
establishment names and canonical NT-ARCH-MONGO-002 §07 categories, without
embedding any personal or user-specific financial data.
"""
from __future__ import annotations

import re
import unicodedata
from dataclasses import dataclass
from datetime import date


def normalize_merchant_text(text: str | None) -> str:
    value = unicodedata.normalize("NFKD", str(text or "")).encode("ascii", "ignore").decode("ascii").upper()
    return re.sub(r"[^A-Z0-9]+", " ", value).strip()


@dataclass(frozen=True)
class PopularMerchantInfo:
    popular_vendor: str
    platform: str | None
    categoria: str | None
    subcategoria: str | None
    brand_aliases: tuple[str, ...] = ()
    context_hints: tuple[str, ...] = ()


# (compiled regex on raw/normalized descriptor, PopularMerchantInfo)
_RULES: tuple[tuple[re.Pattern[str], PopularMerchantInfo], ...] = tuple(
    (re.compile(pattern, re.IGNORECASE), info)
    for pattern, info in (
        (
            r"\b(?:IFD\s*\*?\s*)?ZAMP\s*SA",
            PopularMerchantInfo(
                "Burger King",
                "iFood",
                "Alimentação",
                "Delivery/Apps",
                brand_aliases=("BURGER KING", "BK", "POPEYES", "ZAMP", "ZAMP SA"),
                context_hints=("IFOOD", "HAMBURGUER", "LANCHE", "DELIVERY"),
            ),
        ),
        (
            r"\b(?:IFD\s*\*?\s*)?PIMENTA\s+VERDE",
            PopularMerchantInfo(
                "KFC / Pizza Hut",
                "iFood",
                "Alimentação",
                "Delivery/Apps",
                brand_aliases=("KFC", "PIZZA HUT", "PIMENTA VERDE", "VIENA", "FRANGO ASSADO"),
                context_hints=("IFOOD", "FRANGO", "PIZZA", "DELIVERY"),
            ),
        ),
        (
            r"\bIFD\s*\*?\s*DROGARIAS?\s+PACHECO|\bDROGARIAS?\s+PACHECO",
            PopularMerchantInfo(
                "Drogarias Pacheco",
                "iFood",
                "Saúde e Cuidados",
                "Farmácia",
                brand_aliases=("DROGARIAS PACHECO", "DROGARIA PACHECO", "PACHECO"),
                context_hints=("FARMACIA", "REMEDIO", "IFOOD"),
            ),
        ),
        (
            r"\bRAIA\s*\d+|\bDROGA\s*RAIA",
            PopularMerchantInfo(
                "Droga Raia",
                None,
                "Saúde e Cuidados",
                "Farmácia",
                brand_aliases=("DROGA RAIA", "DROGARAIA", "RAIA"),
                context_hints=("FARMACIA", "REMEDIO"),
            ),
        ),
        (
            r"\bARAUJO\s+LOJA|\bDROGARIA\s+ARAUJO",
            PopularMerchantInfo(
                "Drogaria Araujo",
                None,
                "Saúde e Cuidados",
                "Farmácia",
                brand_aliases=("DROGARIA ARAUJO", "ARAUJO"),
                context_hints=("FARMACIA", "REMEDIO"),
            ),
        ),
        (
            r"\bCAPPTA\s*\*?\s*LANCHONETE\s+LANB|\bLANCHONETE\s+LANCHE\s+BEM|\bLANCHE\s+BEM(?:CONTAGEM|\b)",
            PopularMerchantInfo(
                "Lanche Bem",
                "Cappta",
                "Alimentação",
                "Restaurantes/Bares",
                brand_aliases=("LANCHE BEM", "LANCHONETE LANCHE BEM", "CAPPTA LANCHONETE LANB"),
                context_hints=("LANCHONETE", "CANTINA", "CAFE", "LANCHE", "ALMOCO"),
            ),
        ),
        (
            r"\bSLO\s+SILVA\s+LOBO",
            PopularMerchantInfo(
                "McDonald's Silva Lobo",
                None,
                "Alimentação",
                "Restaurantes/Bares",
                brand_aliases=("MCDONALDS", "MC DONALDS", "MEQUI", "MCDONALDS SILVA LOBO", "SLO SILVA LOBO"),
                context_hints=("HAMBURGUER", "LANCHE", "FAST FOOD"),
            ),
        ),
        (
            r"\bMC\s*DONALD'?S?|\bARCOS\s+DOURADOS",
            PopularMerchantInfo(
                "McDonald's",
                None,
                "Alimentação",
                "Restaurantes/Bares",
                brand_aliases=("MCDONALDS", "MC DONALDS", "MEQUI", "ARCOS DOURADOS"),
                context_hints=("HAMBURGUER", "LANCHE", "FAST FOOD"),
            ),
        ),
        (
            r"\bMEP\s*\*?\s*DA\s+BOCA|\bMERCADO\s+DA\s+BOCA",
            PopularMerchantInfo(
                "Mercado da Boca",
                None,
                "Alimentação",
                "Restaurantes/Bares",
                brand_aliases=("MERCADO DA BOCA", "DA BOCA", "DA BOCA SAVASSI"),
                context_hints=("SAVASSI", "RESTAURANTE", "BAR"),
            ),
        ),
        (
            r"\bESTORIL\s+CONVENIENCIA",
            PopularMerchantInfo(
                "Estoril Conveniência",
                None,
                "Alimentação",
                "Supermercado/Feira/Açougue",
                brand_aliases=("ESTORIL CONVENIENCIA", "ESTORIL"),
                context_hints=("CONVENIENCIA", "MERCADO", "PADARIA"),
            ),
        ),
        (
            r"\bMARKET\s*4\s*U",
            PopularMerchantInfo(
                "market4u",
                None,
                "Alimentação",
                "Supermercado/Feira/Açougue",
                brand_aliases=("MARKET4U", "MARKET 4U", "MERCADINHO CONDOMINIO"),
                context_hints=("MERCADINHO", "MINIMERCADO", "MERCADO"),
            ),
        ),
        (
            r"\bSANTO\s+PAO",
            PopularMerchantInfo(
                "Padaria Santo Pão",
                None,
                "Alimentação",
                "Padaria",
                brand_aliases=("SANTO PAO", "PADARIA SANTO PAO"),
                context_hints=("PADARIA", "CAFE"),
            ),
        ),
        (
            r"\bMP\s*\*?\s*PADARIA",
            PopularMerchantInfo(
                "Padaria",
                "Mercado Pago",
                "Alimentação",
                "Padaria",
                brand_aliases=("MP PADARIA", "PADARIA"),
                context_hints=("PAO", "CAFE", "LANCHE"),
            ),
        ),
        (
            r"\bMP\s*\*?\s*GARCIA|\bDIEGO\s+SILAS\s+GARCIA",
            PopularMerchantInfo(
                "Cantina Garcia",
                "Mercado Pago",
                "Alimentação",
                "Restaurantes/Bares",
                brand_aliases=("CANTINA GARCIA", "MP GARCIA", "DIEGO SILAS GARCIA", "GARCIA"),
                context_hints=("CANTINA", "LANCHE", "CAFE"),
            ),
        ),
        (
            r"\bMATEUS\s*SILVA(?:CONTAGEM|\b)",
            PopularMerchantInfo(
                "Mateus Silva",
                None,
                "Alimentação",
                "Restaurantes/Bares",
                brand_aliases=("MATEUS SILVA",),
                context_hints=("LANCHE", "CANTINA", "CAFE"),
            ),
        ),
        (
            r"\bSACOLAO\s*ABC",
            PopularMerchantInfo(
                "Sacolão ABC",
                None,
                "Alimentação",
                "Supermercado/Feira/Açougue",
                brand_aliases=("SACOLAO ABC", "SACOLAO"),
                context_hints=("HORTIFRUTI", "FEIRA", "MERCADO"),
            ),
        ),
        (
            r"\bSUPERMERCADOS?\s+BH",
            PopularMerchantInfo(
                "Supermercados BH",
                None,
                "Alimentação",
                "Supermercado/Feira/Açougue",
                brand_aliases=("SUPERMERCADOS BH", "SUPERMERCADO BH"),
                context_hints=("SUPERMERCADO", "MERCADO"),
            ),
        ),
        (
            r"\bBIG\s+FOODS\s+COMERCIO|\bBIG\s+FOODS\b",
            PopularMerchantInfo(
                "Big Foods",
                None,
                "Alimentação",
                "Restaurantes/Bares",
                brand_aliases=("BIG FOODS",),
                context_hints=("ALMOCO", "RESTAURANTE", "LANCHE"),
            ),
        ),
        (
            r"\bPOSTO\s+FENIX",
            PopularMerchantInfo(
                "Posto Fênix",
                None,
                "Transporte",
                "Combustível/Recarga",
                brand_aliases=("POSTO FENIX", "FENIX"),
                context_hints=("POSTO", "GASOLINA", "COMBUSTIVEL", "ETANOL", "ABASTECIMENTO"),
            ),
        ),
        (
            r"\bDP\s+BETIM\s+ROTA\s+NORTE|\bROTA\s+NORTE",
            PopularMerchantInfo(
                "Posto Rota Norte",
                None,
                "Transporte",
                "Combustível/Recarga",
                brand_aliases=("POSTO ROTA NORTE", "ROTA NORTE", "DP BETIM ROTA NORTE", "REDE DOM PEDRO"),
                context_hints=("POSTO", "GASOLINA", "COMBUSTIVEL", "ETANOL", "ABASTECIMENTO"),
            ),
        ),
        (
            r"\b99APP|\*\s*99APP",
            PopularMerchantInfo(
                "99",
                "99",
                "Transporte",
                "Uber/99/Táxi",
                brand_aliases=("99", "99APP", "99POP", "99 TAXI"),
                context_hints=("CORRIDA", "TRANSPORTE", "TAXI"),
            ),
        ),
        (
            r"\bUBER\b",
            PopularMerchantInfo(
                "Uber",
                None,
                "Transporte",
                "Uber/99/Táxi",
                brand_aliases=("UBER", "UBER TRIP"),
                context_hints=("CORRIDA", "TRANSPORTE"),
            ),
        ),
        (
            r"\bAZUL\s+LINHAS",
            PopularMerchantInfo(
                "Azul Linhas Aéreas",
                None,
                "Lazer e Estilo",
                "Viagens/Hospedagem",
                brand_aliases=("AZUL LINHAS AEREAS", "AZUL", "VOEAZUL"),
                context_hints=("PASSAGEM", "VOO", "VIAGEM"),
            ),
        ),
        (
            r"\bATEND\s+MED\s+BH",
            PopularMerchantInfo(
                "Atendimento Médico BH Centro",
                None,
                "Saúde e Cuidados",
                "Consultas/Exames/Terapia",
                brand_aliases=("ATEND MED BH", "ATENDIMENTO MEDICO BH"),
                context_hints=("CONSULTA", "MEDICO", "CLINICA", "EXAME"),
            ),
        ),
        (
            r"\bPETZ\b",
            PopularMerchantInfo(
                "Petz",
                None,
                "Pets",
                "Ração e Petiscos",
                brand_aliases=("PETZ",),
                context_hints=("PET SHOP", "RACAO", "PET"),
            ),
        ),
        (
            r"\bAMAZON\s*PRIME",
            PopularMerchantInfo(
                "Amazon Prime",
                None,
                "Lazer e Estilo",
                "Streaming/Assinaturas Digitais",
                brand_aliases=("AMAZON PRIME", "PRIME VIDEO"),
                context_hints=("ASSINATURA", "STREAMING"),
            ),
        ),
        (
            r"\bAMAZON\s+(?:MARKETP|BR)",
            PopularMerchantInfo(
                "Amazon",
                None,
                "Compras",
                "Eletrônicos/Eletrodomésticos",
                brand_aliases=("AMAZON", "AMAZON BR"),
                context_hints=("COMPRA ONLINE",),
            ),
        ),
        (
            r"\bMERCADO\s*LIVRE",
            PopularMerchantInfo(
                "Mercado Livre",
                None,
                "Compras",
                "Eletrônicos/Eletrodomésticos",
                brand_aliases=("MERCADO LIVRE", "MERCADOLIVRE", "MELI"),
                context_hints=("COMPRA ONLINE",),
            ),
        ),
        (
            r"\bGOOGLE\s+ONE",
            PopularMerchantInfo(
                "Google One",
                None,
                "Educação",
                "Idiomas e Softwares",
                brand_aliases=("GOOGLE ONE",),
                context_hints=("GOOGLE", "ARMAZENAMENTO", "NUVEM"),
            ),
        ),
        (
            r"\bTOTAL\s*PASS",
            PopularMerchantInfo(
                "TotalPass",
                None,
                "Saúde e Cuidados",
                "Academia/Esportes",
                brand_aliases=("TOTALPASS", "TOTAL PASS"),
                context_hints=("ACADEMIA", "TREINO"),
            ),
        ),
        (
            r"\bHDI\s+SEGUROS",
            PopularMerchantInfo(
                "HDI Seguros",
                None,
                "Transporte",
                "IPVA/Seguro",
                brand_aliases=("HDI SEGUROS", "HDI", "SEGURO AUTO"),
                context_hints=("SEGURO CARRO", "SEGURO"),
            ),
        ),
        (
            r"\bDSC\s+CONDOMINIAL",
            PopularMerchantInfo(
                "Condomínio",
                None,
                "Habitação",
                "Condomínio",
                brand_aliases=("CONDOMINIO", "DSC CONDOMINIAL", "TAXA DE CONDOMINIO", "BOLETO CONDOMINIO"),
                context_hints=("PREDIO", "APARTAMENTO"),
            ),
        ),
        (
            r"\bSUPERGASBRAS",
            PopularMerchantInfo(
                "Supergasbras (Gás)",
                None,
                "Habitação",
                "Água/Gás",
                brand_aliases=("SUPERGASBRAS", "GAS", "CONTA DE GAS"),
                context_hints=("GAS ENCANADO",),
            ),
        ),
        (
            r"\bDA\s+CEMIG|\bCEMIG\b",
            PopularMerchantInfo(
                "Cemig",
                None,
                "Habitação",
                "Energia",
                brand_aliases=("CEMIG", "CONTA DE LUZ", "LUZ", "ENERGIA ELETRICA"),
                context_hints=("ENERGIA",),
            ),
        ),
        (
            r"\bDA\s+TIM\s+CELU|\bTIM\s+CELULAR|\bTIM\s+SA\b",
            PopularMerchantInfo(
                "TIM",
                None,
                "Habitação",
                "Internet/TV",
                brand_aliases=("TIM", "CONTA TIM", "PLANO TIM", "TIM CELULAR"),
                context_hints=("CELULAR", "TELEFONE"),
            ),
        ),
        (
            r"\bICAM365",
            PopularMerchantInfo(
                "iCam365",
                None,
                "Lazer e Estilo",
                "Streaming/Assinaturas Digitais",
                brand_aliases=("ICAM365", "ICAM 365"),
                context_hints=("CAMERA", "APP"),
            ),
        ),
        (
            r"\bPAYPAL\s*\*?\s*GOOGLE\s+CHATGP|\bCHATGPT\b|\bOPENAI\b",
            PopularMerchantInfo(
                "ChatGPT",
                None,
                "Educação",
                "Idiomas e Softwares",
                brand_aliases=("CHATGPT", "CHAT GPT", "OPENAI"),
                context_hints=("IA", "ASSINATURA"),
            ),
        ),
        (
            r"\bPAYPAL\s*\*?\s*GOOGLE\s+TWITTE|\bTWITTER\b|\bX\s+CORP",
            PopularMerchantInfo(
                "X (Twitter)",
                None,
                "Lazer e Estilo",
                "Streaming/Assinaturas Digitais",
                brand_aliases=("TWITTER", "X PREMIUM", "X TWITTER"),
                context_hints=("ASSINATURA",),
            ),
        ),
        (
            r"\bPAYPAL\s*\*?\s*GOOGLE\s+SOFASC|\bSOFASCORE",
            PopularMerchantInfo(
                "SofaScore",
                None,
                "Lazer e Estilo",
                "Streaming/Assinaturas Digitais",
                brand_aliases=("SOFASCORE", "SOFA SCORE"),
                context_hints=("FUTEBOL", "APP"),
            ),
        ),
        (
            r"\bPAYPAL\s*\*?\s*GOOGLE\s+NIAGAR|\bNIAGARA\s+LAUNCHER",
            PopularMerchantInfo(
                "Niagara Launcher",
                None,
                "Educação",
                "Idiomas e Softwares",
                brand_aliases=("NIAGARA LAUNCHER", "NIAGARA"),
                context_hints=("APP", "ANDROID"),
            ),
        ),
        (
            r"\bPAYPAL\s*\*?\s*GOOGLE\s+YAHOO",
            PopularMerchantInfo(
                "Yahoo",
                None,
                "Lazer e Estilo",
                "Streaming/Assinaturas Digitais",
                brand_aliases=("YAHOO",),
                context_hints=("ASSINATURA",),
            ),
        ),
        (
            r"\bPAYPAL\s*\*?\s*GOOGLE(?:\s+(?:PRODUT|PLAY)|\s*$)",
            PopularMerchantInfo(
                "Google Play",
                None,
                "Lazer e Estilo",
                "Streaming/Assinaturas Digitais",
                brand_aliases=("GOOGLE PLAY", "PLAY STORE"),
                context_hints=("ASSINATURA", "GOOGLE"),
            ),
        ),
        (
            r"\bPAYPAL\s*\*?\s*GOOGLE\s+\w+",
            PopularMerchantInfo(
                "Google Play",
                None,
                "Lazer e Estilo",
                "Streaming/Assinaturas Digitais",
                brand_aliases=(),
                context_hints=("ASSINATURA", "GOOGLE"),
            ),
        ),
        (
            r"\bSCP\s+MAIS",
            PopularMerchantInfo(
                "Seguro Cartão Santander (SCP Mais)",
                None,
                "Finanças/Impostos",
                "Tarifas/Anuidade",
                brand_aliases=("SCP MAIS", "SEGURO CARTAO SANTANDER"),
                context_hints=("SEGURO CARTAO",),
            ),
        ),
        (
            r"\bSEG\s+CARTAO\s+PROTEGIDO|\bSEGURO\s+CARTAO\b",
            PopularMerchantInfo(
                "Seguro Cartão Protegido",
                None,
                "Finanças/Impostos",
                "Tarifas/Anuidade",
                brand_aliases=("SEG CARTAO PROTEGIDO", "CARTAO PROTEGIDO", "SEGURO CARTAO"),
                context_hints=("SEGURO CARTAO",),
            ),
        ),
        (
            r"\bEST\s+ANUIDADE\s+DIFERENCIADA",
            PopularMerchantInfo(
                "Estorno de Anuidade",
                None,
                "Finanças/Impostos",
                "Tarifas/Anuidade",
                brand_aliases=("ESTORNO ANUIDADE", "EST ANUIDADE DIFERENCIADA", "DESCONTO ANUIDADE"),
                context_hints=("ANUIDADE",),
            ),
        ),
        (
            r"\bMensalidade\s*-\s*Plano|\bANUIDADE\s+DIFERENCIADA",
            PopularMerchantInfo(
                "Anuidade Cartão",
                None,
                "Finanças/Impostos",
                "Tarifas/Anuidade",
                brand_aliases=("ANUIDADE CARTAO", "ANUIDADE DIFERENCIADA", "MENSALIDADE CARTAO"),
                context_hints=("ANUIDADE",),
            ),
        ),
        (
            r"\bREMUNERACAO\s*/?\s*SALARIO",
            PopularMerchantInfo(
                "Salário",
                None,
                "Receitas",
                "Renda Principal",
                brand_aliases=("SALARIO", "REMUNERACAO", "ADIANTAMENTO SALARIAL", "PAGAMENTO SALARIO"),
                context_hints=("RENDA", "EMPRESA"),
            ),
        ),
        (
            r"\bREND\s+PAGO\s+APLIC\s+AUT",
            PopularMerchantInfo(
                "Rendimento Conta",
                None,
                "Receitas",
                "Renda Extra",
                brand_aliases=("RENDIMENTO CONTA", "REND PAGO APLIC AUT MAIS"),
                context_hints=("RENDIMENTO",),
            ),
        ),
        (
            r"\bJUROS\s+SALDO\s+UTILIZ",
            PopularMerchantInfo(
                "Juros Limite da Conta",
                None,
                "Finanças/Impostos",
                "Juros/Multas/IOF",
                brand_aliases=("JUROS LIMITE", "JUROS CHEQUE ESPECIAL", "JUROS SALDO UTILIZ"),
                context_hints=("JUROS", "LIMITE"),
            ),
        ),
        (
            r"\bIOF\b",
            PopularMerchantInfo(
                "IOF",
                None,
                "Finanças/Impostos",
                "Juros/Multas/IOF",
                brand_aliases=("IOF", "IMPOSTO OPERACOES FINANCEIRAS"),
                context_hints=("IMPOSTO",),
            ),
        ),
    )
)

_IFD_PREFIX_RE = re.compile(r"^\s*IFD\s*\*\s*(.+)$", re.IGNORECASE)
_BANK_SETTLEMENT_PREFIX_RE = re.compile(
    r"^\s*(?:PIX\b|COMPRA\s+CARTAO\s+DEB\b|PAG\s+BOLETO\b|TED\b|DEB\b)",
    re.IGNORECASE,
)
_EMBEDDED_DDMM_RE = re.compile(r"(?<!\d)(\d{2})/(\d{2})(?!\s*/\s*\d)")


def _clean_ifood_submerchant(raw_tail: str) -> str:
    cleaned = re.sub(r"\b\d{6,}\b", "", raw_tail).strip()
    cleaned = re.sub(
        r"(?:BELO\s*HORIZONT\w*|BELO\s*H\b|BEL\b|CONTAGEM\w*|BETIM\w*|SAO\s*PAULO\w*|LTDA\w*|EIRELI\w*|MEI\b)+$",
        "",
        cleaned,
        flags=re.IGNORECASE,
    ).strip(" -*./")
    if not cleaned:
        return "iFood (Restaurante)"
    title = " ".join(w.capitalize() if len(w) > 2 else w.lower() for w in cleaned.split())
    return f"{title} (iFood)"


def classify_official_descriptor(description: str, bank_category_hint: str = "") -> PopularMerchantInfo:
    desc = (description or "").strip()
    for regex, info in _RULES:
        if regex.search(desc):
            platform = info.platform
            if info.popular_vendor == "Lanche Bem" and not desc.upper().startswith("CAPPTA"):
                platform = None
            if info.popular_vendor == "Cantina Garcia" and not desc.upper().startswith("MP"):
                platform = None
            if info.popular_vendor == "Drogarias Pacheco" and not desc.upper().startswith("IFD"):
                platform = None
            return PopularMerchantInfo(
                popular_vendor=info.popular_vendor,
                platform=platform,
                categoria=info.categoria,
                subcategoria=info.subcategoria,
                brand_aliases=info.brand_aliases,
                context_hints=info.context_hints,
            )

    m_ifd = _IFD_PREFIX_RE.match(desc)
    if m_ifd:
        sub_name = _clean_ifood_submerchant(m_ifd.group(1))
        base_alias = normalize_merchant_text(sub_name.replace("(iFood)", ""))
        aliases = (base_alias,) if len(base_alias) >= 4 else ()
        return PopularMerchantInfo(
            popular_vendor=sub_name,
            platform="iFood",
            categoria="Alimentação",
            subcategoria="Delivery/Apps",
            brand_aliases=aliases,
            context_hints=("IFOOD", "DELIVERY", "RESTAURANTE", "LANCHE", "ALMOCO", "JANTAR"),
        )

    if "CANCELAMENTO PARCIAL" in desc.upper():
        is_food = "restaurante" in (bank_category_hint or "").lower()
        return PopularMerchantInfo(
            popular_vendor="Estorno Parcial",
            platform="iFood" if is_food else None,
            categoria="Alimentação" if is_food else "Compras",
            subcategoria="Delivery/Apps" if is_food else "Eletrônicos/Eletrodomésticos",
            brand_aliases=("ESTORNO PARCIAL", "CANCELAMENTO PARCIAL"),
            context_hints=("ESTORNO", "REEMBOLSO", "IFOOD" if is_food else "COMPRA"),
        )

    if re.match(r"^PIX\s+TRANSF\s+", desc, flags=re.IGNORECASE):
        person = re.sub(r"\s*\d{2}/\d{2}$", "", re.sub(r"^PIX\s+TRANSF\s+", "", desc, flags=re.IGNORECASE)).strip().title()
        norm_person = normalize_merchant_text(person)
        return PopularMerchantInfo(
            popular_vendor=f"{person} (PIX)",
            platform=None,
            categoria="Receitas",
            subcategoria="Reembolsos",
            brand_aliases=(norm_person,) if len(norm_person) >= 3 else (),
            context_hints=("PIX", "REEMBOLSO"),
        )

    if re.match(r"^PIX\s+QRS\s+", desc, flags=re.IGNORECASE):
        body = re.sub(r"\s*(?:GE)?\d{2}/\d{2}$", "", re.sub(r"^PIX\s+QRS\s+", "", desc, flags=re.IGNORECASE)).strip()
        is_mp = "MERCADO PAG" in body.upper()
        title = "Mercado Pago (PIX QR)" if is_mp else f"{body.title()} (PIX)"
        norm_body = normalize_merchant_text(body)
        return PopularMerchantInfo(
            popular_vendor=title,
            platform="Mercado Pago" if is_mp else None,
            categoria="Compras" if is_mp else "Alimentação",
            subcategoria="Eletrônicos/Eletrodomésticos" if is_mp else "Restaurantes/Bares",
            brand_aliases=(norm_body,) if len(norm_body) >= 4 else (),
            context_hints=("PIX", "QRCODE"),
        )

    if re.match(r"^PIX\s+\d{2}\.\d{3}\.\d{3}\s+", desc, flags=re.IGNORECASE):
        person = re.sub(r"^PIX\s+[\d./-]+\s+", "", desc, flags=re.IGNORECASE).strip().title()
        norm_person = normalize_merchant_text(person)
        return PopularMerchantInfo(
            popular_vendor=f"{person} (PIX no Cartão)",
            platform=None,
            categoria="Alimentação",
            subcategoria="Restaurantes/Bares",
            brand_aliases=(norm_person,) if len(norm_person) >= 4 else (),
            context_hints=("PIX", "CARTAO"),
        )

    hint = (bank_category_hint or "").lower()
    cat, subcat = None, None
    if "supermercado" in hint:
        cat, subcat = "Alimentação", "Supermercado/Feira/Açougue"
    elif "restaurante" in hint:
        cat, subcat = "Alimentação", "Restaurantes/Bares"
    elif "saúde" in hint or "saude" in hint:
        cat, subcat = "Saúde e Cuidados", "Consultas/Exames/Terapia"
    elif "serviços" in hint or "servicos" in hint:
        cat, subcat = "Finanças/Impostos", "Tarifas/Anuidade"

    return PopularMerchantInfo(
        popular_vendor=desc,
        platform=None,
        categoria=cat,
        subcategoria=subcat,
    )


def brand_keys_for_text(text: str | None) -> set[str]:
    """Return canonical popular-brand keys matched by raw or already-normalized text."""
    raw = str(text or "").strip()
    norm = normalize_merchant_text(raw)
    if not norm:
        return set()
    for regex, info in _RULES:
        if regex.search(raw) or regex.search(norm):
            if not info.brand_aliases:
                return set()
            return {
                normalize_merchant_text(info.popular_vendor),
                *(normalize_merchant_text(a) for a in info.brand_aliases if a),
            } - {""}
    keys: set[str] = set()
    for _regex, info in _RULES:
        for alias in info.brand_aliases:
            alias_norm = normalize_merchant_text(alias)
            if not alias_norm:
                continue
            if norm == alias_norm or (
                len(alias_norm) >= 4
                and re.search(rf"(?:^| ){re.escape(alias_norm)}(?: |$)", norm)
            ):
                keys.add(normalize_merchant_text(info.popular_vendor))
                keys.add(alias_norm)
                break
    return keys


def popular_alias_match(left: str | None, right: str | None) -> bool:
    """True when two descriptors/names map to the same canonical popular brand."""
    left_keys = brand_keys_for_text(left)
    if not left_keys:
        return False
    right_keys = brand_keys_for_text(right)
    return bool(left_keys & right_keys)


def extract_embedded_purchase_date(description: str | None, posted_date: date | None) -> date | None:
    """Extract embedded DD/MM purchase date on weekend-settled bank debit/PIX lines."""
    if not description or not posted_date:
        return posted_date
    if not _BANK_SETTLEMENT_PREFIX_RE.match(description) or "PERIODO" in description.upper():
        return posted_date
    matches = list(_EMBEDDED_DDMM_RE.finditer(description))
    if not matches:
        return posted_date
    # Prefer the last valid DD/MM token on PIX lines or the first valid DD/MM after DEB MC
    for match in reversed(matches):
        day_num, month_num = int(match.group(1)), int(match.group(2))
        for year_candidate in (posted_date.year, posted_date.year - 1):
            try:
                candidate = date(year_candidate, month_num, day_num)
            except ValueError:
                continue
            delta = (posted_date - candidate).days
            if 0 <= delta <= 5:
                return candidate
    return posted_date

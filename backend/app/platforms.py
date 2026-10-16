import re
import unicodedata


PLATFORMS = {
    "ifood": ("merchant-ifood", "iFood"),
    "99food": ("merchant-99food", "99Food"),
    "rappi": ("merchant-rappi", "Rappi"),
    "sem_parar": ("merchant-sem-parar", "Sem Parar"),
}


def platform_identity(value: str | None) -> tuple[str, str] | None:
    raw = (value or "").strip().upper()
    plain = unicodedata.normalize("NFKD", raw).encode("ascii", "ignore").decode("ascii")
    normalized = re.sub(r"[^A-Z0-9]+", " ", plain).strip()
    if raw.startswith("IFD*") or raw.startswith("IFOOD (") or "IFOOD.COM" in raw or re.search(r"\bIFOOD\b", normalized):
        return PLATFORMS["ifood"]
    if re.search(r"\b99\s*FOODS?\b", normalized):
        return PLATFORMS["99food"]
    if re.search(r"\bRAPPI\b", normalized):
        return PLATFORMS["rappi"]
    if re.search(r"\bSEM\s+PARAR\b", normalized):
        return PLATFORMS["sem_parar"]
    return None

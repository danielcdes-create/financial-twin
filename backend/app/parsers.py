from datetime import datetime, timezone, timedelta
from zoneinfo import ZoneInfo, ZoneInfoNotFoundError
from decimal import Decimal, InvalidOperation

FORMATS=("%d/%m/%Y %H:%M:%S","%d/%m/%Y %H:%M","%d/%m/%Y","%Y-%m-%d %H:%M:%S","%Y-%m-%d")

def parse_brl(value)->Decimal:
    if isinstance(value,Decimal): return value.quantize(Decimal("0.01"))
    text=str(value).strip().replace("R$","").replace("BRL","").replace(" ","")
    if "," in text: text=text.replace(".","").replace(",",".")
    try: return Decimal(text).quantize(Decimal("0.01"))
    except InvalidOperation as exc: raise ValueError("invalid BRL amount") from exc

def parse_datetime(value)->datetime:
    if isinstance(value,datetime): dt=value
    else:
        text=str(value).strip()
        try: dt=datetime.fromisoformat(text.replace("Z","+00:00"))
        except ValueError:
            dt=None
            for fmt in FORMATS:
                try: dt=datetime.strptime(text,fmt); break
                except ValueError: pass
            if dt is None: raise ValueError("unsupported date format")
    if dt.tzinfo is not None: return dt
    try: local_tz=ZoneInfo("America/Sao_Paulo")
    except ZoneInfoNotFoundError: local_tz=timezone(timedelta(hours=-3))
    return dt.replace(tzinfo=local_tz)

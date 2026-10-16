"""UTC instants and Brazilian civil dates are distinct domain types (NT-001)."""
from datetime import date, datetime, time, timedelta, timezone
from zoneinfo import ZoneInfo
LOCAL_ZONE = ZoneInfo('America/Sao_Paulo')
def utc_instant(value):
    if isinstance(value, str): value = datetime.fromisoformat(value.replace('Z', '+00:00'))
    if not isinstance(value, datetime): raise ValueError('Informe um instante ISO 8601 válido.')
    return value.replace(tzinfo=timezone.utc) if value.tzinfo is None else value.astimezone(timezone.utc)
def civil_date(value):
    if isinstance(value, datetime): return value.date()
    if isinstance(value, date): return value
    return date.fromisoformat(value[:10])

def local_date(value): return utc_instant(value).astimezone(LOCAL_ZONE).date()
def today(): return datetime.now(LOCAL_ZONE).date()
def day_start(value: date): return datetime.combine(value, time.min, tzinfo=LOCAL_ZONE).astimezone(timezone.utc)
def date_filter(start=None, end=None):
    if start and end and start > end: raise ValueError('A data inicial deve ser anterior à data final.')
    result = {}
    if start: result['$gte'] = day_start(start)
    if end: result['$lt'] = day_start(end + timedelta(days=1))
    return result

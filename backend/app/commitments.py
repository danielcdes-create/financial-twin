"""User-managed future commitments. A schedule never represents a cash debit."""
import calendar
from datetime import date, datetime, timedelta, timezone
from decimal import Decimal
from typing import Literal
from uuid import UUID, uuid4

from pydantic import BaseModel, ConfigDict, Field, model_validator, model_serializer


Frequency = Literal["semanal", "mensal", "trimestral", "semestral", "anual"]
# Development floor. Not a stored document and not editable in the app.
# Before 01/10/2026 an unlinked charge stays assumed paid. From the next civil month,
# the boundary is the first day of that month, so the previous month is no longer pending.
COMMITMENT_CUTOFF = date(2026, 10, 1)


def commitment_cutoff(on: date | None = None) -> date:
    """Assumed-paid boundary for a civil day. It never moves earlier than 2026-10-01."""
    if on is None:
        from app.financial_time import today as civil_today
        on = civil_today()
    start = date(on.year, on.month, 1)
    return start if start > COMMITMENT_CUTOFF else COMMITMENT_CUTOFF
# Automatic suggestions only. Manual search is not limited to this window.
SUGGESTION_BEFORE_DAYS = 7
SUGGESTION_AFTER_DAYS = 10


def suggestion_window(due: date) -> tuple[date, date]:
    """Charges outside this window stay out of automatic suggestions."""
    return due - timedelta(days=SUGGESTION_BEFORE_DAYS), due + timedelta(days=SUGGESTION_AFTER_DAYS)


def automatic_suggestion(paid: date, due: date, *, related: bool, amount: Decimal, expected: Decimal, same_card: bool = False) -> bool:
    """A suggestion needs the due window and either a similar name or a very close amount."""
    start, end = suggestion_window(due)
    if paid < start or paid > end:
        return False
    very_close = abs(abs(amount) - expected) <= max(Decimal("1"), expected * Decimal("0.02"))
    exact_card = same_card and abs(abs(amount) - expected) <= Decimal("0.01")
    return related or very_close or exact_card


def presentation_state(due: date, linked: bool, as_of: date, cutoff: date | None = None) -> str:
    """Derive a parcel state. Dates are civil dates in America/Sao_Paulo."""
    if cutoff is None:
        cutoff = commitment_cutoff()
    if linked:
        return 'Pago vinculado'
    if due < cutoff:
        return 'Pago (assumido)'
    if due < as_of:
        return 'Vencido · sem vínculo'
    return 'Previsto'


def pending_kind(due: date, linked: bool, as_of: date, cutoff: date | None = None) -> str | None:
    """Pendente when the commitment is still charged, the due is on or after the cutoff, and nothing is linked.

    Today only chooses the label. Vencida is before today; a_vencer is today or later.
    """
    if cutoff is None:
        cutoff = commitment_cutoff()
    if linked or due < cutoff:
        return None
    return 'vencida' if due < as_of else 'a_vencer'


def linked_due_dates(item: dict) -> set[str]:
    found = set()
    for link in item.get('linked_transactions') or []:
        raw = link.get('due_date') or link.get('occurrence_due_date')
        if raw:
            found.add(str(raw)[:10])
    return found


def is_open_ended(item: dict) -> bool:
    """Automatic renewal without an end date has no finite restante. Do not sum the long projection."""
    if item.get('status') != 'ativo' or item.get('end_date'):
        return False
    if item.get('kind') in {'parcelamento', 'obrigacao'}:
        return False
    return item.get('renewal_status') == 'automatica'


class CommitmentIn(BaseModel):
    model_config = ConfigDict(extra="forbid")
    kind: Literal["assinatura", "parcelamento", "obrigacao"]
    obligation_plan: Literal["a_vista", "parcelado"] | None = None
    cash_due_date: date | None = None
    name: str = Field(min_length=1, max_length=160)
    purchase_date: date
    next_due_date: date
    amount: Decimal = Field(gt=0, max_digits=12, decimal_places=2)
    amount_mode: Literal['fixed', 'last_three_bills'] | None = None
    frequency: Frequency = "mensal"
    installment_count: int | None = Field(default=None, ge=1, le=600)
    next_installment_number: int | None = Field(default=None, ge=1, le=601)
    payment_method: Literal["cartao", "debito", "pix", "boleto", "paypal", "outro"]
    billing_platform: str | None = Field(default=None, max_length=120)
    card_id: str | None = None
    account_id: str | None = None
    renewal_status: Literal["automatica", "manual", "nao_renovar", "nao_aplicavel"] = "nao_aplicavel"
    remaining_charges: int | None = Field(default=None, ge=0, le=600)
    end_date: date | None = None
    status: Literal["ativo", "pausado", "cancelado", "concluido"] = "ativo"
    category: str | None = Field(default=None, max_length=120)
    notes: str | None = Field(default=None, max_length=1000)
    creation_id: UUID | None = None
    request_id: UUID | None = None
    revision: int | None = Field(default=None, ge=1)

    @model_serializer(mode='wrap')
    def compatible_payload(self, handler):
        value = handler(self)
        if self.amount_mode is None:
            value.pop('amount_mode', None)
        return value

    @model_validator(mode="after")
    def coherent(self):
        if self.amount_mode == 'last_three_bills' and (self.kind != 'assinatura' or self.frequency != 'mensal' or not self.account_id or self.card_id):
            raise ValueError('A média das três contas exige compromisso mensal vinculado à conta bancária.')
        if self.kind == "obrigacao":
            if self.renewal_status != "nao_aplicavel":
                raise ValueError("Obrigação pontual não renova.")
            if self.amount_mode == "last_three_bills":
                raise ValueError("Obrigação pontual usa o valor total informado.")
            if self.cash_due_date is None:
                raise ValueError("Informe a data à vista.")
            if self.obligation_plan == "parcelado":
                if self.installment_count is None or not 2 <= self.installment_count <= 48:
                    raise ValueError("Informe de 2 a 48 parcelas.")
                if self.next_installment_number is None:
                    self.next_installment_number = 1
                if self.frequency != "mensal":
                    raise ValueError("O parcelamento hipotético é mensal.")
                completed_sentinel = self.status == "concluido" and self.next_installment_number == self.installment_count + 1
                if self.next_installment_number > self.installment_count and not completed_sentinel:
                    raise ValueError("A próxima parcela excede o total de parcelas.")
            elif self.installment_count is not None or self.next_installment_number is not None:
                raise ValueError("À vista não tem número de parcelas.")
        elif self.kind == "parcelamento":
            if self.installment_count is None or self.next_installment_number is None:
                raise ValueError("Informe o total e o número da próxima parcela.")
            completed_sentinel = self.status == 'concluido' and self.next_installment_number == self.installment_count + 1
            if self.next_installment_number > self.installment_count and not completed_sentinel:
                raise ValueError("A próxima parcela excede o total de parcelas.")
            if self.renewal_status != "nao_aplicavel":
                raise ValueError("Renovação só se aplica a assinaturas.")
        elif self.installment_count is not None or self.next_installment_number is not None:
            raise ValueError("Número de parcelas só se aplica a parcelamentos.")
        if self.kind == "assinatura" and self.renewal_status == "nao_renovar" and self.remaining_charges is None:
            raise ValueError("Informe quantas cobranças restam até o término da assinatura.")
        if self.status == "cancelado" and self.remaining_charges:
            raise ValueError("Um compromisso cancelado não pode ter cobranças restantes. Use ativo e sem renovação se elas ainda serão cobradas.")
        if self.kind == "parcelamento" and self.remaining_charges is not None:
            raise ValueError("Cobranças restantes só se aplicam a assinaturas.")
        if self.next_due_date < self.purchase_date:
            raise ValueError("O próximo compromisso não pode anteceder a compra.")
        if self.end_date and self.end_date < self.purchase_date:
            raise ValueError("A data de término não pode anteceder a contratação.")
        if self.payment_method == "cartao" and not self.card_id:
            raise ValueError("Selecione o cartão usado no pagamento.")
        return self


def advance(day: date, frequency: Frequency, steps: int) -> date:
    if frequency == "semanal":
        return day + timedelta(weeks=steps)
    months = {"mensal": 1, "trimestral": 3, "semestral": 6, "anual": 12}[frequency] * steps
    index = day.year * 12 + day.month - 1 + months
    year, month = divmod(index, 12)
    month += 1
    return date(year, month, min(day.day, calendar.monthrange(year, month)[1]))


def as_date(value: date | datetime | str) -> date:
    if isinstance(value, datetime):
        return value.date()
    if isinstance(value, date):
        return value
    return date.fromisoformat(value[:10])


def bson_date(value: date | None) -> datetime | None:
    return datetime(value.year, value.month, value.day, tzinfo=timezone.utc) if value else None


def schedule(item: dict, count: int = 12, *, offset: int = 0) -> list[dict]:
    if item.get("status") != "ativo" or historical_occurrence_conflicts(item):
        return []
    start = as_date(item["next_due_date"])
    anchor_day=item.get("schedule_anchor_day") or start.day
    anchor_end=item.get("schedule_anchor_end",False)
    def occurrence(offset):
        result=advance(start,item["frequency"],offset)
        if item["frequency"]!="semanal":
            last=calendar.monthrange(result.year,result.month)[1]
            result=result.replace(day=last if anchor_end else min(anchor_day,last))
        return result
    first_number = item.get("next_installment_number") or 1
    total = item.get("installment_count")
    remaining = item.get("remaining_charges") if item.get("renewal_status") == "nao_renovar" else None
    available = count if total is None else min(count, total - first_number + 1 - offset)
    if remaining is not None:
        available = min(available, remaining - offset)
    if item.get("renewal_status") == "manual" or (item.get("kind") == "obrigacao" and not total):
        available = min(available, 1 - offset)
    end = as_date(item["end_date"]) if item.get("end_date") else None
    settled_dates = {str(link.get("due_date"))[:10] for link in item.get("linked_transactions", [])
                     if link.get("due_date") and link.get("settled_next")}
    def row_amount(number):
        if item.get("kind") != "obrigacao" or not total:
            return float(item["amount"])
        total_amount = Decimal(str(item["amount"]))
        base = (total_amount / Decimal(total)).quantize(Decimal("0.01"), rounding="ROUND_DOWN")
        if number == total:
            return float(total_amount - base * (total - 1))
        return float(base)
    rows = [{"due_date": occurrence(offset).isoformat(),
             "amount": row_amount(first_number + offset),
             "installment_number": first_number + offset if total else None,
             "status": "previsto"} for offset in range(offset, offset + max(0, available))
            if occurrence(offset).isoformat() not in settled_dates
            and (end is None or occurrence(offset) <= end) and (not item.get('future_cutoff') or occurrence(offset) < as_date(item['future_cutoff']))]
    return apply_installment_overrides(item, rows)


def apply_installment_overrides(item: dict, rows: list[dict]) -> list[dict]:
    """Partial future edits stay on the commitment. The base due date is the key. Cancelled rows leave the projection."""
    overrides = item.get("installment_overrides") or {}
    if not isinstance(overrides, dict) or not overrides:
        return rows
    output = []
    for row in rows:
        patch = overrides.get(row["due_date"])
        if not isinstance(patch, dict):
            output.append(row)
            continue
        if patch.get("cancelled"):
            continue
        updated = dict(row)
        if patch.get("amount") not in (None, ""):
            updated["amount"] = float(Decimal(str(patch["amount"])))
        if patch.get("due_date"):
            updated["due_date"] = str(patch["due_date"])[:10]
        output.append(updated)
    return output


def payment_occurrences(item, paid_date):
    """Only future schedule identities can be selected without inventing history."""
    return [row for row in schedule(item, 3) if abs((paid_date - as_date(row["due_date"])).days) <= 7]


def next_occurrence_date(item):
    start = as_date(item["next_due_date"])
    result = advance(start, item["frequency"], 1)
    if item["frequency"] != "semanal":
        last = calendar.monthrange(result.year, result.month)[1]
        result = result.replace(day=last if item.get("schedule_anchor_end") else min(item.get("schedule_anchor_day") or start.day, last))
    return result


def occurrence_already_linked(item, due_date, paid_date):
    for link in item.get("linked_transactions", []):
        if link.get("due_date") and str(link["due_date"])[:10] == due_date:
            return True
        # Old payment-only links do not prove which occurrence was settled.
        # Preserve them and ask for review instead of assigning one by inference.
        if not link.get("due_date") and str(link.get("paid_date") or link.get("period_key") or "")[:7] in {due_date[:7], paid_date.strftime("%Y-%m")}:
            return True
    return False


def historical_occurrence_conflicts(item):
    """A historical-only link cannot consume the current/future agenda."""
    if item.get("status") not in {"ativo", "pausado"} or not item.get("next_due_date"):
        return []
    next_due = as_date(item["next_due_date"])
    conflicts = []
    for link in item.get("linked_transactions", []):
        if not link.get("occurrence_key") or link.get("settled_next") is not False or not link.get("due_date"):
            continue
        if as_date(link["due_date"]) >= next_due:
            conflicts.append(link)
    return conflicts


def schedule_from(item: dict, start: date, count: int = 600) -> list[dict]:
    """Seek by frequency without consuming the display limit on overdue history."""
    anchor = as_date(item['next_due_date'])
    if item['frequency'] == 'semanal':
        offset = max(0, (start - anchor).days // 7)
    else:
        step = {'mensal': 1, 'trimestral': 3, 'semestral': 6, 'anual': 12}[item['frequency']]
        months = (start.year - anchor.year) * 12 + start.month - anchor.month
        offset = max(0, months // step)
    return [row for row in schedule(item, count + 1, offset=offset) if row['due_date'] >= start.isoformat()][:count]


def stored_document(body: CommitmentIn, existing: dict | None = None) -> dict:
    now = datetime.now(timezone.utc)
    value = body.model_dump(mode="json")
    value.pop('request_id',None)
    value.pop("revision", None)
    value.pop("creation_id", None)
    value["name"] = value["name"].strip()
    value["amount"] = str(body.amount)
    if existing and body.amount_mode is None:
        value['amount_mode'] = existing.get('amount_mode')
    for field in ("purchase_date", "next_due_date", "end_date"):
        value[field] = bson_date(getattr(body, field))
    if body.kind == "obrigacao":
        value["cash_due_date"] = bson_date(body.cash_due_date)
    else:
        value.pop("obligation_plan", None)
        value.pop("cash_due_date", None)
    value.update({"commitment_id": existing["commitment_id"] if existing else str(uuid4()),
                  "created_at": existing["created_at"] if existing else now,
                  "updated_at": now,
                  "source": "mobile_manual",
                  "linked_transactions": existing.get("linked_transactions", []) if existing else []})
    same_schedule=bool(existing and as_date(existing["next_due_date"])==body.next_due_date and existing["frequency"]==body.frequency)
    value["schedule_anchor_day"]=(existing.get("schedule_anchor_day") or as_date(existing["next_due_date"]).day) if same_schedule else body.next_due_date.day
    value["schedule_anchor_end"]=existing.get("schedule_anchor_end",False) if same_schedule else body.next_due_date.day==calendar.monthrange(body.next_due_date.year,body.next_due_date.month)[1]
    value["revision"] = (existing.get("revision") or 1) + 1 if existing else 1
    if body.status=='cancelado':value['cancelled_at']=(existing or {}).get('cancelled_at') or value['updated_at']
    if existing:
        for field in ("purchase_provisional_id", "purchase_transaction_id", "purchase_fingerprint", "installment_overrides"):
            if existing.get(field) and field not in value:
                value[field] = existing[field]
    return value


def future_pending_total(item: dict) -> Decimal | None:
    if historical_occurrence_conflicts(item):
        return None
    if item.get("status") == "cancelado" and item.get("remaining_charges"):
        return None
    if item.get("status") != "ativo":
        return Decimal("0")
    if is_open_ended(item):
        return None
    return sum((Decimal(str(part["amount"])) for part in schedule(item, 600)), Decimal("0"))


def pending_rows(item: dict, as_of: date, cutoff: date | None = None) -> list[dict]:
    """Finite or windowed occurrences that still have to be paid. Nothing is stored."""
    if item.get('status') != 'ativo' or historical_occurrence_conflicts(item):
        return []
    if cutoff is None:
        cutoff = commitment_cutoff()
    linked = linked_due_dates(item)
    rows = []
    for part in schedule(item, 600):
        due = as_date(part['due_date'])
        kind = pending_kind(due, part['due_date'] in linked, as_of, cutoff)
        if kind is None:
            continue
        rows.append({**part, 'kind': kind})
    return rows


def commitment_restante(item: dict, as_of: date, cutoff: date | None = None):
    """Sum of pendentes. None means the series has no end date, so the UI shows a dash."""
    if is_open_ended(item):
        return None
    rows = pending_rows(item, as_of, cutoff)
    total = sum((Decimal(str(row['amount'])) for row in rows), Decimal('0'))
    return {'amount': total, 'count': len(rows), 'last_due': rows[-1]['due_date'] if rows else None, 'rows': rows}

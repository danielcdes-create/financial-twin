from datetime import datetime
from typing import Any, Literal
from uuid import UUID
from decimal import Decimal, InvalidOperation
from pydantic import BaseModel, ConfigDict, Field, field_validator

class ParsedPayload(BaseModel):
    """Validate interpreted domain fields without rewriting the original evidence."""
    model_config = ConfigDict(extra="allow")
    direction: Literal["expense", "income"] = "expense"
    amount: Decimal | None = None
    currency: Literal["BRL"] = "BRL"
    merchant_raw: str | None = Field(default=None, strict=True)
    merchant: str | None = Field(default=None, strict=True)
    payment_method: Literal['pix','cartao_credito','cartao_debito','dinheiro','boleto','transferencia'] | None = None
    event_timestamp: datetime | None = None

    @field_validator("event_timestamp", mode="before")
    @classmethod
    def timestamp(cls,value):
        if value is not None and not isinstance(value,(str,datetime)):
            raise ValueError("Data do evento deve ser um instante ISO ou datetime.")
        return value

    @field_validator("amount")
    @classmethod
    def monetary_value(cls,value):
        if value is None: return value
        if not value.is_finite() or not 0 < value <= Decimal("100000000"):
            raise ValueError("O valor deve ser positivo, finito e até 100000000.")
        try: rounded=value.quantize(Decimal("0.01"))
        except InvalidOperation as exc: raise ValueError("Valor financeiro inválido.") from exc
        if rounded == 0: raise ValueError("O valor deve corresponder a pelo menos um centavo.")
        return value

def validate_parsed_payload(value):
    ParsedPayload.model_validate(value or {})

class RawEventIn(BaseModel):
    model_config = ConfigDict(extra="forbid")
    event_id: UUID
    source_type: Literal["android_notification", "receipt", "invoice", "boleto", "pdf", "csv", "xlsx", "manual"]
    source_provider: str = Field(min_length=1, max_length=120)
    captured_at: datetime
    raw_payload: dict[str, Any]
    parsed_payload: dict[str, Any] | None = None
    parser_version: str | None = Field(default=None, max_length=80)

    @field_validator("parsed_payload")
    @classmethod
    def domain_payload(cls,value):
        validate_parsed_payload(value)
        return value

class EventAck(BaseModel):
    event_id: UUID
    processing_status: Literal["pending", "already_received"]
    received_at: datetime

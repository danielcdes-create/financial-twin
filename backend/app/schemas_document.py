from datetime import datetime as DateTime
from typing import Optional
from pydantic import BaseModel, ConfigDict, Field, field_validator
from app.extracted_currency import normalize_extracted_currency
from app.parsers import parse_datetime

class ReceiptItem(BaseModel):
    model_config=ConfigDict(extra="forbid")
    description:str=Field(min_length=1,max_length=200)
    amount:float=Field(ge=0)

class DocumentExtraction(BaseModel):
    model_config=ConfigDict(extra="forbid")
    document_type:str=Field(min_length=1,max_length=40)
    merchant:Optional[str]=Field(default=None,max_length=200)
    datetime:Optional[DateTime]=None
    timezone_evidence:Optional[str]=Field(default=None,max_length=80)
    total:Optional[float]=Field(default=None,ge=0)
    currency:Optional[str]=Field(default=None,max_length=40)
    items:list[ReceiptItem]=Field(default_factory=list,max_length=250)
    confidence:Optional[float]=Field(default=None,ge=0,le=1)

    @field_validator('datetime', mode='before')
    @classmethod
    def document_time(cls, value):
        # Receipts print a local wall clock. A missing zone is never an UTC claim.
        return parse_datetime(value) if value is not None else None

    @field_validator('currency', mode='before')
    @classmethod
    def canonical_currency(cls,value):
        return normalize_extracted_currency(value)

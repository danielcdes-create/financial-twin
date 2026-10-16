from typing import Any, Protocol

class FinancialDataProvider(Protocol):
    def list_accounts(self)->list[dict[str,Any]]: ...
    def list_transactions(self)->list[dict[str,Any]]: ...

class MockFinancialProvider:
    """Deterministic empty provider for development without external connectivity."""
    def __init__(self,accounts=None,transactions=None):
        self._accounts=list(accounts or []);self._transactions=list(transactions or [])
    def list_accounts(self): return list(self._accounts)
    def list_transactions(self): return list(self._transactions)

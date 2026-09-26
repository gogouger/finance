import json
import os
import urllib.error
import urllib.request
from typing import Any
from uuid import uuid4


class PlaidProviderError(RuntimeError):
    def __init__(self, message: str, code: str = "PROVIDER_ERROR"):
        super().__init__(message)
        self.code = code


class PlaidProvider:
    environment = "sandbox"

    def create_link_token(
        self, client_user_id: str, products: list[str]
    ) -> dict[str, Any]:
        raise NotImplementedError

    def exchange_public_token(self, public_token: str) -> dict[str, str]:
        raise NotImplementedError

    def get_item(self, access_token: str) -> dict[str, Any]:
        raise NotImplementedError

    def remove_item(self, access_token: str) -> None:
        raise NotImplementedError

    def accounts_get(self, access_token: str) -> dict[str, Any]:
        raise NotImplementedError

    def transactions_sync(self, access_token: str, cursor: str | None) -> dict[str, Any]:
        raise NotImplementedError

    def investments_holdings_get(self, access_token: str) -> dict[str, Any]:
        raise NotImplementedError

    def investments_transactions_get(
        self,
        access_token: str,
        start_date: str,
        end_date: str,
        *,
        offset: int = 0,
        count: int = 500,
    ) -> dict[str, Any]:
        raise NotImplementedError

    def verify_webhook(self, body: bytes, verification: str | None) -> bool:
        raise NotImplementedError

    def payroll_provider_coverage(self, provider_name: str) -> str:
        return "unknown"

    def create_payroll_link_token(self, client_user_id: str) -> dict[str, Any]:
        raise NotImplementedError

    def payroll_income_get(self, user_id: str) -> dict[str, Any]:
        raise NotImplementedError


class FakePlaidProvider(PlaidProvider):
    def __init__(self):
        self._mutation_raised: set[str] = set()

    def create_link_token(
        self, client_user_id: str, products: list[str]
    ) -> dict[str, Any]:
        return {
            "link_token": f"link-sandbox-{uuid4()}",
            "expiration": "2099-01-01T00:00:00Z",
        }

    def exchange_public_token(self, public_token: str) -> dict[str, str]:
        suffix = next(
            (
                marker
                for marker in ("mutation", "investments", "accounting", "recurring")
                if marker in public_token
            ),
            str(uuid4()),
        )
        return {
            "access_token": f"access-sandbox-{suffix}",
            "item_id": f"item-sandbox-{uuid4()}",
        }

    def get_item(self, access_token: str) -> dict[str, Any]:
        return {"item": {"error": None}, "status": {}}

    def remove_item(self, access_token: str) -> None:
        return None

    def accounts_get(self, access_token: str) -> dict[str, Any]:
        if "accounting" in access_token:
            base_balance = {
                "current": 5000.0,
                "available": 5000.0,
                "limit": None,
                "iso_currency_code": "USD",
            }
            return {
                "accounts": [
                    {"account_id": "checking", "name": "Checking", "type": "depository", "subtype": "checking", "balances": base_balance},
                    {"account_id": "savings", "name": "Savings", "type": "depository", "subtype": "savings", "balances": base_balance},
                    {"account_id": "credit", "name": "Credit card", "type": "credit", "subtype": "credit card", "balances": {**base_balance, "current": 200.0, "limit": 10000.0}},
                ]
            }
        if "investments" in access_token:
            return {
                "accounts": [
                    {
                        "account_id": "account-brokerage",
                        "name": "Plaid Brokerage",
                        "mask": "0000",
                        "type": "investment",
                        "subtype": "brokerage",
                        "balances": {
                            "current": 1760.0,
                            "available": None,
                            "limit": None,
                            "iso_currency_code": "USD",
                        },
                    }
                ]
            }
        return {"accounts": [{"account_id": "account-checking", "name": "Plaid Checking", "mask": "0000", "type": "depository", "subtype": "checking", "balances": {"current": 1250.0, "available": 1200.0, "limit": None, "iso_currency_code": "USD"}}]}

    def transactions_sync(self, access_token: str, cursor: str | None) -> dict[str, Any]:
        if "recurring" in access_token:
            if cursor is not None:
                return {"added": [], "modified": [], "removed": [], "next_cursor": cursor, "has_more": False}
            common = {"account_id": "account-checking", "iso_currency_code": "USD", "pending": False}
            rows = [
                ("stream-jun", "2026-06-05", "StreamFlix", 15.0),
                ("stream-jul", "2026-07-05", "StreamFlix", 15.0),
                ("stream-aug", "2026-08-05", "StreamFlix", 15.0),
                ("stream-sep", "2026-09-05", "StreamFlix", 15.0),
                ("tax-jan", "2026-01-15", "Douglas County Treasurer", 1200.0),
                ("tax-apr", "2026-04-15", "Douglas County Treasurer", 1200.0),
                ("tax-jul", "2026-07-15", "Douglas County Treasurer", 1200.0),
                ("tax-oct", "2026-10-15", "Douglas County Treasurer", 1200.0),
                ("insurance-2024", "2024-11-01", "Home Shield Insurance", 1200.0),
                ("insurance-2025", "2025-11-01", "Home Shield Insurance", 1200.0),
                ("one-off", "2026-09-19", "Neighborhood Hardware", 89.0),
            ]
            return {
                "added": [
                    {
                        **common,
                        "transaction_id": transaction_id,
                        "date": transaction_date,
                        "name": merchant,
                        "merchant_name": merchant,
                        "amount": amount,
                        "personal_finance_category": {
                            "primary": "GENERAL_SERVICES",
                            "detailed": "GENERAL_SERVICES_OTHER_GENERAL_SERVICES",
                        },
                    }
                    for transaction_id, transaction_date, merchant, amount in rows
                ],
                "modified": [],
                "removed": [],
                "next_cursor": "recurring-v1",
                "has_more": False,
            }
        if "accounting" in access_token:
            if cursor == "accounting-v1":
                return {
                    "added": [],
                    "modified": [{
                        "transaction_id": "grocery-posted",
                        "pending_transaction_id": "grocery-pending",
                        "account_id": "checking",
                        "date": "2026-09-21",
                        "name": "Mountain Market",
                        "merchant_name": "Mountain Market",
                        "amount": 53.0,
                        "pending": False,
                        "iso_currency_code": "USD",
                        "personal_finance_category": {"primary": "FOOD_AND_DRINK", "detailed": "FOOD_AND_DRINK_GROCERIES"},
                    }],
                    "removed": [],
                    "next_cursor": "accounting-v2",
                    "has_more": False,
                }
            if cursor is not None:
                return {"added": [], "modified": [], "removed": [], "next_cursor": cursor, "has_more": False}
            common = {"iso_currency_code": "USD", "pending": False}
            transaction = lambda transaction_id, account_id, date, name, amount, primary, detailed, **extra: {
                **common, "transaction_id": transaction_id, "account_id": account_id,
                "date": date, "name": name, "amount": amount,
                "personal_finance_category": {"primary": primary, "detailed": detailed},
                **extra,
            }
            return {
                "added": [
                    transaction("grocery-pending", "checking", "2026-09-20", "Mountain Market", 50.0, "FOOD_AND_DRINK", "FOOD_AND_DRINK_GROCERIES", merchant_name="Mountain Market", pending=True),
                    transaction("grocery-posted", "checking", "2026-09-21", "Mountain Market", 52.0, "FOOD_AND_DRINK", "FOOD_AND_DRINK_GROCERIES", merchant_name="Mountain Market", pending_transaction_id="grocery-pending"),
                    transaction("transfer-out", "checking", "2026-09-21", "Transfer to savings", 500.0, "TRANSFER_OUT", "TRANSFER_OUT_ACCOUNT_TRANSFER"),
                    transaction("transfer-in", "savings", "2026-09-21", "Transfer from checking", -500.0, "TRANSFER_IN", "TRANSFER_IN_ACCOUNT_TRANSFER"),
                    transaction("card-payment-out", "checking", "2026-09-22", "Capital One payment", 200.0, "LOAN_PAYMENTS", "LOAN_PAYMENTS_CREDIT_CARD_PAYMENT"),
                    transaction("card-payment-in", "credit", "2026-09-22", "Payment received", -200.0, "TRANSFER_IN", "TRANSFER_IN_ACCOUNT_TRANSFER"),
                    transaction("store-purchase", "credit", "2026-09-10", "Trail Store", 100.0, "GENERAL_MERCHANDISE", "GENERAL_MERCHANDISE_OTHER_GENERAL_MERCHANDISE", merchant_name="Trail Store"),
                    transaction("store-refund", "credit", "2026-09-23", "Trail Store refund", -100.0, "INCOME", "INCOME_OTHER_INCOME", merchant_name="Trail Store"),
                    transaction("paycheck", "checking", "2026-09-15", "Payroll", -2000.0, "INCOME", "INCOME_WAGES"),
                    transaction("reimbursable", "credit", "2026-09-18", "Team meal", 40.0, "FOOD_AND_DRINK", "FOOD_AND_DRINK_RESTAURANT"),
                    transaction("shared", "credit", "2026-09-18", "Shared supplies", 60.0, "GENERAL_MERCHANDISE", "GENERAL_MERCHANDISE_OTHER_GENERAL_MERCHANDISE"),
                    transaction("business", "credit", "2026-09-18", "Business service", 70.0, "GENERAL_SERVICES", "GENERAL_SERVICES_OTHER_GENERAL_SERVICES"),
                    transaction("excluded", "credit", "2026-09-18", "Excluded purchase", 80.0, "GENERAL_MERCHANDISE", "GENERAL_MERCHANDISE_OTHER_GENERAL_MERCHANDISE"),
                ],
                "modified": [], "removed": [], "next_cursor": "accounting-v1", "has_more": False,
            }
        if "investments" in access_token:
            return {"added": [], "modified": [], "removed": [], "next_cursor": "done", "has_more": False}
        if cursor == "page-2" and access_token.endswith("mutation") and access_token not in self._mutation_raised:
            self._mutation_raised.add(access_token)
            raise PlaidProviderError("pagination changed", "TRANSACTIONS_SYNC_MUTATION_DURING_PAGINATION")
        if cursor is None:
            return {"added": [{"transaction_id": "transaction-1", "account_id": "account-checking", "date": "2026-09-20", "name": "Groceries", "amount": 50.0, "pending": False, "iso_currency_code": "USD"}], "modified": [], "removed": [], "next_cursor": "page-2", "has_more": True}
        if cursor == "page-2":
            return {"added": [{"transaction_id": "transaction-2", "account_id": "account-checking", "date": "2026-09-21", "name": "Paycheck", "amount": -2000.0, "pending": False, "iso_currency_code": "USD"}], "modified": [], "removed": [], "next_cursor": "done", "has_more": False}
        return {"added": [], "modified": [], "removed": [], "next_cursor": cursor or "done", "has_more": False}

    def investments_holdings_get(self, access_token: str) -> dict[str, Any]:
        return {
            "securities": [
                {
                    "security_id": "security-index",
                    "name": "Example Total Market Fund",
                    "ticker_symbol": "TOTAL",
                    "type": "mutual fund",
                    "iso_currency_code": "USD",
                },
                {
                    "security_id": "security-bond",
                    "name": "Example Bond Fund",
                    "ticker_symbol": "BOND",
                    "type": "mutual fund",
                    "iso_currency_code": "USD",
                },
            ],
            "holdings": [
                {
                    "account_id": "account-brokerage",
                    "security_id": "security-index",
                    "quantity": 10.0,
                    "institution_price": 120.0,
                    "institution_value": 1200.0,
                    "cost_basis": 900.0,
                    "cost_basis_as_of": "2025-12-31",
                    "iso_currency_code": "USD",
                },
                {
                    "account_id": "account-brokerage",
                    "security_id": "security-bond",
                    "quantity": 7.0,
                    "institution_price": 80.0,
                    "institution_value": 560.0,
                    "cost_basis": None,
                    "iso_currency_code": "USD",
                },
            ],
            # Historical observations are supplied by the fake adapter so the
            # service-boundary suite can exercise period return calculations.
            "valuation_history": [
                {"date": "2025-01-01", "value": 1000.0},
                {"date": "2025-06-30", "value": 1100.0, "timing": "pre_flow"},
                {"date": "2025-12-31", "value": 1760.0},
            ],
            "benchmarks": {
                "SPY": [
                    {"date": "2025-01-01", "value": 100.0},
                    {"date": "2025-12-31", "value": 112.0},
                ],
                "VTI": [
                    {"date": "2025-01-01", "value": 100.0},
                    {"date": "2025-12-31", "value": 110.0},
                ],
            },
        }

    def investments_transactions_get(
        self,
        access_token: str,
        start_date: str,
        end_date: str,
        *,
        offset: int = 0,
        count: int = 500,
    ) -> dict[str, Any]:
        activities = [
                {"investment_transaction_id": "activity-contribution", "account_id": "account-brokerage", "date": "2025-06-30", "type": "cash", "subtype": "deposit", "amount": 500.0, "iso_currency_code": "USD"},
                {"investment_transaction_id": "activity-withdrawal", "account_id": "account-brokerage", "date": "2025-06-30", "type": "cash", "subtype": "withdrawal", "amount": 100.0, "iso_currency_code": "USD"},
                {"investment_transaction_id": "activity-dividend", "account_id": "account-brokerage", "date": "2025-09-30", "type": "cash", "subtype": "dividend", "amount": 40.0, "iso_currency_code": "USD"},
                {"investment_transaction_id": "activity-fee", "account_id": "account-brokerage", "date": "2025-10-31", "type": "fee", "subtype": "management fee", "amount": 10.0, "iso_currency_code": "USD"},
            ]
        return {
            "investment_transactions": activities[offset : offset + count],
            "total_investment_transactions": len(activities),
        }

    def verify_webhook(self, body: bytes, verification: str | None) -> bool:
        return verification == "fake-valid"

    def payroll_provider_coverage(self, provider_name: str) -> str:
        if provider_name.casefold() in {"quickbooks workforce", "quickbooks payroll"}:
            return "supported"
        return "unsupported"

    def create_payroll_link_token(self, client_user_id: str) -> dict[str, Any]:
        return {
            "link_token": f"payroll-link-sandbox-{uuid4()}",
            "expiration": "2099-01-01T00:00:00Z",
            "user_id": f"payroll-user-{client_user_id[:16]}",
        }

    def payroll_income_get(self, user_id: str) -> dict[str, Any]:
        if os.environ.get("PAYROLL_FAKE_ERROR") == "1":
            raise PlaidProviderError(
                "payroll income temporarily unavailable",
                "INCOME_PROVIDER_UNAVAILABLE",
            )
        return {
            "items": [
                {
                    "institution_name": "QuickBooks Workforce",
                    "payroll_income": [
                        {
                            "account_id": "payroll-account-1",
                            "pay_stubs": [
                                {
                                    "pay_date": "2026-09-15",
                                    "gross_earnings": {"current_amount": 5000.0},
                                    "net_pay": {"current_amount": 3500.0},
                                    "taxes": {
                                        "breakdown": [
                                            {
                                                "description": "Federal income tax",
                                                "current_amount": 900.0,
                                                "iso_currency_code": "USD",
                                            },
                                            {
                                                "description": "Social Security",
                                                "current_amount": 300.0,
                                                "iso_currency_code": "USD",
                                            },
                                        ]
                                    },
                                    "deductions": {
                                        "breakdown": [
                                            {
                                                "description": "401(k)",
                                                "current_amount": 300.0,
                                                "iso_currency_code": "USD",
                                            }
                                        ]
                                    },
                                }
                            ],
                        }
                    ],
                }
            ]
        }


class UnconfiguredPlaidProvider(PlaidProvider):
    def _missing(self):
        raise PlaidProviderError("Plaid Sandbox credentials are not configured")

    def create_link_token(
        self, client_user_id: str, products: list[str]
    ) -> dict[str, Any]:
        self._missing()

    def exchange_public_token(self, public_token: str) -> dict[str, str]:
        self._missing()

    def get_item(self, access_token: str) -> dict[str, Any]:
        self._missing()

    def remove_item(self, access_token: str) -> None:
        self._missing()

    def accounts_get(self, access_token: str) -> dict[str, Any]:
        self._missing()

    def transactions_sync(self, access_token: str, cursor: str | None) -> dict[str, Any]:
        self._missing()

    def investments_holdings_get(self, access_token: str) -> dict[str, Any]:
        self._missing()

    def investments_transactions_get(
        self,
        access_token: str,
        start_date: str,
        end_date: str,
        *,
        offset: int = 0,
        count: int = 500,
    ) -> dict[str, Any]:
        self._missing()

    def verify_webhook(self, body: bytes, verification: str | None) -> bool:
        return False

    def create_payroll_link_token(self, client_user_id: str) -> dict[str, Any]:
        self._missing()

    def payroll_income_get(self, user_id: str) -> dict[str, Any]:
        self._missing()


class ReleaseGatedPlaidProvider(UnconfiguredPlaidProvider):
    def _missing(self):
        raise PlaidProviderError(
            "Plaid production linking is disabled until every release gate passes"
        )


class HttpPlaidProvider(PlaidProvider):
    def __init__(self, client_id: str, secret: str, environment: str = "sandbox"):
        self._client_id = client_id
        self._secret = secret
        if environment not in {"sandbox", "development", "production"}:
            raise ValueError("unsupported Plaid environment")
        self.environment = environment
        self._base_url = f"https://{environment}.plaid.com"

    def _post(self, path: str, payload: dict[str, Any]) -> dict[str, Any]:
        request = urllib.request.Request(
            f"{self._base_url}{path}",
            data=json.dumps(payload).encode(),
            headers={
                "Content-Type": "application/json",
                "PLAID-CLIENT-ID": self._client_id,
                "PLAID-SECRET": self._secret,
                "Plaid-Version": "2020-09-14",
            },
            method="POST",
        )
        try:
            with urllib.request.urlopen(request, timeout=20) as response:
                return json.load(response)
        except (urllib.error.HTTPError, urllib.error.URLError, TimeoutError) as error:
            raise PlaidProviderError("Plaid request failed") from error

    def create_link_token(
        self, client_user_id: str, products: list[str]
    ) -> dict[str, Any]:
        payload: dict[str, Any] = {
            "client_name": "Gordon Finance",
            "country_codes": ["US"],
            "language": "en",
            "products": products,
            "user": {"client_user_id": client_user_id},
            "webhook": "https://finance.gordongouger.com/api/public/plaid/webhook",
        }
        if "transactions" in products:
            payload["transactions"] = {"days_requested": 730}
        return self._post("/link/token/create", payload)

    def exchange_public_token(self, public_token: str) -> dict[str, str]:
        return self._post(
            "/item/public_token/exchange", {"public_token": public_token}
        )

    def get_item(self, access_token: str) -> dict[str, Any]:
        return self._post("/item/get", {"access_token": access_token})

    def remove_item(self, access_token: str) -> None:
        self._post("/item/remove", {"access_token": access_token})

    def accounts_get(self, access_token: str) -> dict[str, Any]:
        return self._post("/accounts/get", {"access_token": access_token})

    def transactions_sync(self, access_token: str, cursor: str | None) -> dict[str, Any]:
        payload: dict[str, Any] = {"access_token": access_token, "count": 500}
        if cursor:
            payload["cursor"] = cursor
        return self._post("/transactions/sync", payload)

    def investments_holdings_get(self, access_token: str) -> dict[str, Any]:
        return self._post("/investments/holdings/get", {"access_token": access_token})

    def investments_transactions_get(
        self,
        access_token: str,
        start_date: str,
        end_date: str,
        *,
        offset: int = 0,
        count: int = 500,
    ) -> dict[str, Any]:
        return self._post(
            "/investments/transactions/get",
            {
                "access_token": access_token,
                "start_date": start_date,
                "end_date": end_date,
                "options": {"count": count, "offset": offset},
            },
        )

    def verify_webhook(self, body: bytes, verification: str | None) -> bool:
        if not verification:
            return False
        import hashlib
        import hmac
        import time

        import jwt

        try:
            header = jwt.get_unverified_header(verification)
            if header.get("alg") != "ES256" or not header.get("kid"):
                return False
            key = self._post(
                "/webhook_verification_key/get", {"key_id": header["kid"]}
            )["key"]
            claims = jwt.decode(
                verification,
                jwt.PyJWK.from_dict(key).key,
                algorithms=["ES256"],
                options={"require": ["iat", "request_body_sha256"]},
            )
            if abs(time.time() - claims["iat"]) > 300:
                return False
            return hmac.compare_digest(
                hashlib.sha256(body).hexdigest(), claims["request_body_sha256"]
            )
        except Exception:
            return False

    def create_payroll_link_token(self, client_user_id: str) -> dict[str, Any]:
        user = self._post("/user/create", {"client_user_id": client_user_id})
        result = self._post(
            "/link/token/create",
            {
                "client_name": "Gordon Finance",
                "country_codes": ["US"],
                "language": "en",
                "products": ["income_verification"],
                "user": {"client_user_id": client_user_id},
                "user_id": user["user_id"],
                "income_verification": {
                    "income_source_types": ["payroll"],
                    "payroll_income": {
                        "flow_types": ["payroll_digital_income"]
                    },
                },
                "webhook": "https://finance.gordongouger.com/api/public/plaid/webhook",
            },
        )
        return {**result, "user_id": user["user_id"]}

    def payroll_income_get(self, user_id: str) -> dict[str, Any]:
        return self._post("/credit/payroll_income/get", {"user_id": user_id})


def create_plaid_provider() -> PlaidProvider:
    if os.environ.get("PLAID_MODE") == "fake":
        return FakePlaidProvider()
    client_id = os.environ.get("PLAID_CLIENT_ID")
    secret = os.environ.get("PLAID_SECRET")
    if not client_id or not secret:
        return UnconfiguredPlaidProvider()
    environment = os.environ.get("PLAID_ENVIRONMENT", "sandbox").lower()
    if environment == "production":
        from .release_gates import production_release_gates_pass

        if not production_release_gates_pass():
            return ReleaseGatedPlaidProvider()
    return HttpPlaidProvider(client_id, secret, environment)

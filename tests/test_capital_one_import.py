import io
from base64 import urlsafe_b64encode
from pathlib import Path

import pytest

from backend.finance_app.capital_one_import import import_capital_one_csv
from backend.finance_app.storage import EncryptedStorage


HEADER = "Transaction Date,Posted Date,Card No.,Description,Category,Debit,Credit\n"


@pytest.fixture
def imported_storage(tmp_path: Path):
    storage = EncryptedStorage(tmp_path, urlsafe_b64encode(b"i" * 32).decode())
    storage.initialize()
    connection = storage.save_connection(
        "owner",
        {
            "provider": "plaid",
            "status": "healthy",
            "connection_type": "credit",
        },
    )
    storage.upsert_financial_record(
        "owner",
        connection["id"],
        "account",
        "account-1",
        {
            "account_id": "account-1",
            "connection_id": connection["id"],
            "type": "credit",
        },
    )
    return storage, connection


def test_import_is_idempotent_and_preserves_equal_source_rows(imported_storage):
    storage, connection = imported_storage
    source = (
        HEADER
        + "2024-01-01,2024-01-02,1234,STORE,Merchandise,10.25,\n"
        + "2024-01-01,2024-01-02,1234,STORE,Merchandise,10.25,\n"
        + "2024-01-03,2024-01-04,9876,PAYMENT,Payment/Credit,,20.00\n"
    )

    first = import_capital_one_csv(storage, connection, io.StringIO(source))
    second = import_capital_one_csv(storage, connection, io.StringIO(source))

    assert first == {
        "connection_id": connection["id"],
        "imported": 3,
        "overlap_skipped": 0,
        "date_start": "2024-01-02",
        "date_end": "2024-01-04",
    }
    assert second["imported"] == 0
    assert second["overlap_skipped"] == 3
    transactions = storage.list_financial_records("owner", "transaction")
    assert len(transactions) == 3
    assert sorted(item["amount"] for item in transactions) == [-20.0, 10.25, 10.25]
    assert {item["source"] for item in transactions} == {"capital_one_csv"}
    assert {item["account_id"] for item in transactions} == {"account-1"}


def test_import_skips_matching_plaid_transaction(imported_storage):
    storage, connection = imported_storage
    storage.upsert_financial_record(
        "owner",
        connection["id"],
        "transaction",
        "plaid-existing",
        {
            "account_id": "account-1",
            "connection_id": connection["id"],
            "transaction_id": "plaid-existing",
            "date": "2024-01-02",
            "name": "Store",
            "amount": 10.25,
            "pending": False,
            "iso_currency_code": "USD",
        },
    )

    result = import_capital_one_csv(
        storage,
        connection,
        io.StringIO(
            HEADER + "2024-01-01,2024-01-02,1234, STORE ,Merchandise,10.25,\n"
        ),
    )

    assert result["imported"] == 0
    assert result["overlap_skipped"] == 1
    assert len(storage.list_financial_records("owner", "transaction")) == 1


def test_import_rejects_ambiguous_or_malformed_input(imported_storage):
    storage, connection = imported_storage
    storage.upsert_financial_record(
        "owner",
        connection["id"],
        "account",
        "account-2",
        {
            "account_id": "account-2",
            "connection_id": connection["id"],
            "type": "credit",
        },
    )
    with pytest.raises(ValueError, match="exactly one credit account"):
        import_capital_one_csv(
            storage,
            connection,
            io.StringIO(HEADER + "2024-01-01,2024-01-02,1234,STORE,Other,1.00,\n"),
        )

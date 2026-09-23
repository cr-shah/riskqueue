import numpy as np
import pytest

from riskqueue.data.validation import (
    DataValidationError,
    validate_scoring_transactions,
    validate_transactions,
)


def test_valid_data_is_sorted(transactions):
    result = validate_transactions(transactions.iloc[::-1])
    assert result.step.tolist() == sorted(result.step)


@pytest.mark.parametrize("column", ["step", "type", "amount", "nameOrig", "nameDest", "isFraud"])
def test_required_columns(transactions, column):
    with pytest.raises(DataValidationError, match="Missing columns"):
        validate_transactions(transactions.drop(columns=column))


@pytest.mark.parametrize("amount", [-1.0, np.inf, -np.inf])
def test_invalid_amounts(transactions, amount):
    transactions.loc[0, "amount"] = amount
    with pytest.raises(DataValidationError, match="amount"):
        validate_transactions(transactions)


def test_unknown_type(transactions):
    transactions.loc[0, "type"] = "WIRE"
    with pytest.raises(DataValidationError, match="Invalid transaction"):
        validate_transactions(transactions)


def test_empty_data_rejected(transactions):
    with pytest.raises(DataValidationError, match="empty"):
        validate_transactions(transactions.iloc[:0])


def test_null_required_value_rejected(transactions):
    transactions.loc[0, "nameOrig"] = None
    with pytest.raises(DataValidationError, match="null"):
        validate_transactions(transactions)


def test_inference_contract_does_not_require_fraud_label(transactions):
    inference = transactions.drop(columns="isFraud")
    result = validate_scoring_transactions(inference)
    assert len(result) == len(inference)


def test_training_contract_does_not_require_synthetic_transaction_id(transactions):
    paysim_shape = transactions.drop(columns="transaction_id")
    result = validate_transactions(paysim_shape)
    assert len(result) == len(paysim_shape)

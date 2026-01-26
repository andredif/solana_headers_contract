"""
Pytest configuration and fixtures for contract testing

This module provides shared fixtures and configuration for all tests.
"""

import pytest
from solders.pubkey import Pubkey


@pytest.fixture
def owner_address():
    """Fixture providing a test owner address"""
    return Pubkey.from_string("11111111111111111111111111111112")


@pytest.fixture
def governance_token_mint():
    """Fixture providing a test governance token mint address"""
    return Pubkey.from_string("11111111111111111111111111111113")


@pytest.fixture
def test_recipient():
    """Fixture providing a test recipient address"""
    return Pubkey.from_string("11111111111111111111111111111114")


@pytest.fixture
def test_payer():
    """Fixture providing a test payer address"""
    return Pubkey.from_string("11111111111111111111111111111115")


@pytest.fixture
def contract_config(owner_address, governance_token_mint):
    """Fixture providing contract configuration"""
    return {
        "owner": owner_address,
        "governance_token_mint": governance_token_mint,
    }


@pytest.fixture
def payment_data(test_recipient):
    """Fixture providing test payment data"""
    return {
        "recipient": test_recipient,
        "expiration_time": 1704067200,
        "payment_amount": 1000000,  # 1 SOL in lamports
    }

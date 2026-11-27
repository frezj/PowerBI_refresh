import pytest
import responses

from pbi_client import PowerBIClient


@pytest.fixture
def api():
    # Fails the test if a registered call was not made
    with responses.RequestsMock() as rsps:
        yield rsps


@pytest.fixture
def sleeps():
    return []


@pytest.fixture
def client(sleeps):
    # No real waits in tests, record them instead
    return PowerBIClient(lambda: 'test-token', sleep=sleeps.append)

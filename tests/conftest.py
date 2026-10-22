from pathlib import Path

import pytest

FIXTURES = Path(__file__).parent / "fixtures"


@pytest.fixture
def photo_fixture():
    return FIXTURES / "photo.jpg"

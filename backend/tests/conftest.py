import os
import pytest

# Override DATABASE_URL for all tests to use the test database
os.environ['DATABASE_URL'] = 'postgresql+psycopg://airfare_user:airfare_password@localhost:5432/airfare_index_test'

import pytest
from decimal import Decimal
from backend.processing.aggregation.route_level_index_service import calculate_route_index, RouteLevelIndexService
from backend.app.db.database import SessionLocal
from backend.app.db.models.index_daily import IndexDaily
from sqlalchemy import select

def test_calculate_route_index_basic():
    # Basic arithmetic mean
    values = [Decimal("110.00"), Decimal("120.00")]
    result = calculate_route_index(values)
    assert result == Decimal("115.00")

def test_calculate_route_index_single():
    # Single available window
    values = [Decimal("110.00")]
    result = calculate_route_index(values)
    assert result == Decimal("110.00")

def test_calculate_route_index_empty():
    # Empty list should raise ValueError
    try:
        calculate_route_index([])
    except ValueError:
        pass
    else:
        pytest.fail("Should have raised ValueError for empty list")

def test_calculate_route_index_weights_unimplemented():
    # Weights are provided but currently unimplemented (raises NotImplementedError)
    try:
        calculate_route_index([Decimal("110")], weights={1: Decimal("1.0")})
    except NotImplementedError:
        pass
    else:
        pytest.fail("Should have raised NotImplementedError for weights")

def test_idempotency_and_aggregation():
    with SessionLocal() as session:
        # Clear old ROUTE_LEVEL records
        session.execute(text("DELETE FROM index_daily WHERE index_level = 'ROUTE_LEVEL'"))
        session.commit()
        
        service = RouteLevelIndexService(db=session)
        # Run once
        count1 = service.compute_route_indices("JUL-2026")
        
        # Run twice
        count2 = service.compute_route_indices("JUL-2026")
        
        # Verify no new rows were created on second run
        # We check total count for ROUTE_LEVEL
        stmt = select(IndexDaily).where(IndexDaily.index_level == "ROUTE_LEVEL")
        total = len(session.scalars(stmt).all())
        assert total == count1
        print(f"Idempotency verified: {total} ROUTE_LEVEL records exist.")

from sqlalchemy import text
if __name__ == "__main__":
    # Simple manual runner since we aren't using a full test suite here
    print("Running basic math tests...")
    test_calculate_route_index_basic()
    test_calculate_route_index_single()
    test_calculate_route_index_empty()
    test_calculate_route_index_weights_unimplemented()
    print("Basic math tests passed!")
    
    print("\nRunning DB integration tests...")
    test_idempotency_and_aggregation()
    print("DB integration tests passed!")

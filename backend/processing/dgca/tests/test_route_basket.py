"""Comprehensive tests for DGCA-derived route basket ingestion.

Covers all 14 required verification areas:
1. Excel loading
2. Required column validation
3. City normalization
4. Route resolution
5. Direction handling
6. Traffic total calculation
7. Share-of-total calculation
8. Basket-weight calculation
9. Weight sum ≈ 1.0
10. Top-25 route count
11. Duplicate prevention
12. Idempotent ingestion
13. Source provenance
14. July 2026 expected values
"""

from datetime import date
from decimal import Decimal
import io
from pathlib import Path
import zipfile
import pytest
from sqlalchemy import create_engine, select
from sqlalchemy.orm import sessionmaker

from backend.app.db.base import Base
import backend.app.db.models  # Register all models with Base
from backend.app.db.models.city import City
from backend.app.db.models.route import Route
from backend.app.db.models.dgca_traffic_data import DGCATrafficData
from backend.app.db.models.route_weight import RouteWeight

from backend.processing.dgca.excel_reader import (
    load_raw_route_basket,
    read_excel_rows,
    DGCAParsingError,
    REQUIRED_COLUMNS,
)
from backend.processing.dgca.route_basket_service import (
    DGCARouteBasketService,
    DEFAULT_EXCEL_PATH,
    REFERENCE_YEAR,
    REFERENCE_MONTH,
    BASE_PERIOD_CODE,
    REPORT_SOURCE,
    PERIOD_START,
    PERIOD_END,
    CITY_NAME_TO_CODE,
)


@pytest.fixture
def excel_path():
    """Return path to the verified Top-25 route basket spreadsheet."""
    path = DEFAULT_EXCEL_PATH
    if not path.exists():
        # Fallback if running from a subdirectory
        candidate = Path(__file__).resolve().parents[4] / "data" / "dgca" / "dgca_july_2026_top25_route_basket.xlsx"
        if candidate.exists():
            return candidate
    return path


@pytest.fixture
def raw_data(excel_path):
    """Load the raw Top-25 records from Excel."""
    return load_raw_route_basket(excel_path)


@pytest.fixture
def db_session():
    """Provide an isolated in-memory SQLite database session for unit tests."""
    engine = create_engine("sqlite:///:memory:")
    Base.metadata.create_all(bind=engine)
    Session = sessionmaker(bind=engine)
    session = Session()

    yield session

    session.close()
    Base.metadata.drop_all(bind=engine)


# ==============================================================================
# 1. EXCEL LOADING
# ==============================================================================
def test_1_excel_loading(excel_path):
    """Verify that the Top-25 spreadsheet loads cleanly and returns records."""
    data = load_raw_route_basket(excel_path)
    assert isinstance(data, list)
    assert len(data) == 25
    assert data[0]["rank"] == 1
    assert data[0]["city_1"] == "Delhi"
    assert data[0]["city_2"] == "Mumbai"


# ==============================================================================
# 2. REQUIRED COLUMN VALIDATION
# ==============================================================================
def test_2_required_column_validation_present(raw_data):
    """Verify all 11 required columns are present in loaded records."""
    expected_keys = {
        "rank",
        "city_1",
        "city_2",
        "passengers_to_city_2",
        "passengers_from_city_2",
        "total_route_traffic",
        "share_of_total_traffic",
        "basket_weight",
        "reference_period",
        "source_organization",
        "access_layer",
    }
    for row in raw_data:
        assert expected_keys.issubset(row.keys())


def test_2_required_column_validation_missing(tmp_path):
    """Verify that an Excel missing any required column raises DGCAParsingError."""
    # Construct a dummy xlsx with missing columns
    sheet_xml = """<?xml version="1.0" encoding="UTF-8" standalone="yes"?>
    <worksheet xmlns="http://schemas.openxmlformats.org/spreadsheetml/2006/main">
        <sheetData>
            <row r="1">
                <c r="A1" t="inlineStr"><is><t>Rank</t></is></c>
                <c r="B1" t="inlineStr"><is><t>City 1</t></is></c>
                <c r="C1" t="inlineStr"><is><t>City 2</t></is></c>
            </row>
        </sheetData>
    </worksheet>"""
    test_file = tmp_path / "missing_cols.xlsx"
    with zipfile.ZipFile(test_file, "w") as zf:
        zf.writestr("xl/worksheets/sheet1.xml", sheet_xml)

    with pytest.raises(DGCAParsingError, match="Missing required columns in Excel"):
        load_raw_route_basket(test_file)


# ==============================================================================
# 3. CITY NORMALIZATION
# ==============================================================================
def test_3_city_normalization(db_session):
    """Verify city normalization correctly maps names/aliases to master City records."""
    service = DGCARouteBasketService(db_session)

    # Test major metro cities
    delhi = service.normalize_city("Delhi")
    assert delhi.city_code == "DEL"
    assert delhi.city_name == "Delhi"
    assert delhi.is_metro is True

    # Test alias normalization (Bengaluru / Bangalore)
    blr1 = service.normalize_city("Bengaluru")
    assert blr1.city_code == "BLR"

    # Test alias returns the exact same city record without duplicate
    blr2 = service.normalize_city("Bangalore")
    assert blr2.city_id == blr1.city_id

    # Test non-metro basket cities
    pune = service.normalize_city("Pune")
    assert pune.city_code == "PNQ"
    assert pune.is_metro is False

    ahmedabad = service.normalize_city("Ahmedabad")
    assert ahmedabad.city_code == "AMD"

    bagdogra = service.normalize_city("Bagdogra")
    assert bagdogra.city_code == "IXB"

    leh = service.normalize_city("Leh")
    assert leh.city_code == "IXL"


def test_3_all_15_basket_cities_normalized(db_session, raw_data):
    """Verify all 15 unique cities in the Top-25 basket normalize cleanly."""
    service = DGCARouteBasketService(db_session)
    unique_names = set()
    for row in raw_data:
        unique_names.add(row["city_1"])
        unique_names.add(row["city_2"])

    assert len(unique_names) == 15

    for city_name in unique_names:
        city = service.normalize_city(city_name)
        assert city.city_id is not None
        assert len(city.city_code) == 3
        assert city.city_code in CITY_NAME_TO_CODE.values()


# ==============================================================================
# 4. ROUTE RESOLUTION (UNDIRECTED CITY PAIRS)
# ==============================================================================
def test_4_route_resolution_undirected(db_session):
    """Verify that route resolution handles undirected city pairs identically."""
    service = DGCARouteBasketService(db_session)
    c1 = service.normalize_city("Delhi")
    c2 = service.normalize_city("Mumbai")

    # First resolution: creates DEL-BOM
    route1, created1 = service.resolve_or_create_route(c1, c2)
    assert created1 is True
    assert route1.route_code == "DEL-BOM"

    # Reverse resolution: BOM and DEL should REUSE route1
    route2, created2 = service.resolve_or_create_route(c2, c1)
    assert created2 is False
    assert route2.route_id == route1.route_id
    assert route2.route_code == "DEL-BOM"


def test_4_route_resolution_reusing_preexisting(db_session):
    """Verify that pre-existing routes in the database are reused."""
    service = DGCARouteBasketService(db_session)
    c1 = service.normalize_city("Delhi")
    c2 = service.normalize_city("Mumbai")

    # Manually pre-seed DEL-BOM
    pre_route = Route(
        origin_city_id=c1.city_id,
        destination_city_id=c2.city_id,
        route_code="DEL-BOM",
        is_active=True,
    )
    db_session.add(pre_route)
    db_session.flush()

    route, created = service.resolve_or_create_route(c1, c2)
    assert created is False
    assert route.route_id == pre_route.route_id


# ==============================================================================
# 5. DIRECTION HANDLING
# ==============================================================================
def test_5_direction_handling_sum(raw_data):
    """Verify Passengers To City 2 + Passengers From City 2 == Total Route Traffic for every row."""
    for row in raw_data:
        to_c2 = row["passengers_to_city_2"]
        from_c2 = row["passengers_from_city_2"]
        total = row["total_route_traffic"]
        assert to_c2 + from_c2 == total, (
            f"Directional sum failed for row {row['rank']} ({row['city_1']}-{row['city_2']}): "
            f"{to_c2} + {from_c2} != {total}"
        )


# ==============================================================================
# 6. TRAFFIC TOTAL CALCULATION
# ==============================================================================
def test_6_traffic_total_calculation(raw_data):
    """Verify total traffic across the Top-25 route basket is approximately 4,296,845."""
    total_traffic = sum(row["total_route_traffic"] for row in raw_data)
    assert total_traffic == 4_296_845


# ==============================================================================
# 7. SHARE-OF-TOTAL CALCULATION
# ==============================================================================
def test_7_share_of_total_calculation(raw_data):
    """Verify total share of national traffic across the Top-25 routes is approximately 35.81%."""
    total_share = sum(row["share_of_total_traffic"] for row in raw_data)
    # 0.358088... -> 35.81%
    share_pct = round(total_share * 100, 2)
    assert share_pct == Decimal("35.81")
    assert Decimal("0.3580") <= total_share <= Decimal("0.3582")


# ==============================================================================
# 8. BASKET-WEIGHT CALCULATION
# ==============================================================================
def test_8_basket_weight_calculation(raw_data):
    """Verify each route's basket weight equals its route traffic divided by total basket traffic."""
    total_traffic = sum(row["total_route_traffic"] for row in raw_data)
    assert total_traffic == 4_296_845

    for row in raw_data:
        expected_weight = Decimal(row["total_route_traffic"]) / Decimal(total_traffic)
        actual_weight = row["basket_weight"]
        # Match within 1e-6 tolerance
        assert abs(actual_weight - expected_weight) < Decimal("0.000001"), (
            f"Weight mismatch for {row['city_1']}-{row['city_2']}: "
            f"actual {actual_weight}, expected {expected_weight}"
        )


# ==============================================================================
# 9. WEIGHT SUM ≈ 1.0
# ==============================================================================
def test_9_weight_sum_approx_one(raw_data):
    """Verify the sum of basket weights across the Top-25 routes equals 1.0 (100%)."""
    raw_sum = sum(row["basket_weight"] for row in raw_data)
    # The raw Decimal values sum exactly to 1.0
    assert raw_sum == Decimal("1.00000000000000000")

    # Quantized to 8 decimal places for database storage
    quantized_sum = sum(
        row["basket_weight"].quantize(Decimal("0.00000001"))
        for row in raw_data
    )
    assert quantized_sum == Decimal("0.99999999")
    assert abs(quantized_sum - Decimal("1.0")) < Decimal("0.000001")


# ==============================================================================
# 10. TOP-25 ROUTE COUNT
# ==============================================================================
def test_10_top_25_route_count(raw_data):
    """Verify exactly 25 distinct routes are loaded with ranks 1 to 25."""
    assert len(raw_data) == 25
    ranks = [row["rank"] for row in raw_data]
    assert ranks == list(range(1, 26))


# ==============================================================================
# 11. DUPLICATE PREVENTION
# ==============================================================================
def test_11_duplicate_prevention(db_session, excel_path):
    """Verify that running ingestion does not create duplicate Route records."""
    service = DGCARouteBasketService(db_session)
    service.ingest_route_basket(excel_path=excel_path, commit=True)

    route_count = db_session.query(Route).count()
    assert route_count == 25

    # Check for duplicate undirected pairs
    all_routes = db_session.query(Route).all()
    pairs = set()
    for r in all_routes:
        pair = frozenset([r.origin_city_id, r.destination_city_id])
        assert pair not in pairs, f"Duplicate route pair found for route_id {r.route_id}"
        pairs.add(pair)


# ==============================================================================
# 12. IDEMPOTENT INGESTION
# ==============================================================================
def test_12_idempotent_ingestion(db_session, excel_path):
    """Verify that executing ingestion twice produces identical, non-duplicated records."""
    service = DGCARouteBasketService(db_session)

    # First run
    summary1 = service.ingest_route_basket(excel_path=excel_path, commit=True)
    assert summary1.routes_created == 25
    assert summary1.routes_reused == 0
    assert summary1.traffic_records_upserted == 25
    assert summary1.weight_records_upserted == 25

    routes_count_1 = db_session.query(Route).count()
    traffic_count_1 = db_session.query(DGCATrafficData).count()
    weights_count_1 = db_session.query(RouteWeight).count()

    assert routes_count_1 == 25
    assert traffic_count_1 == 25
    assert weights_count_1 == 25

    # Second run (Idempotency test)
    summary2 = service.ingest_route_basket(excel_path=excel_path, commit=True)
    assert summary2.routes_created == 0
    assert summary2.routes_reused == 25
    assert summary2.traffic_records_upserted == 25
    assert summary2.weight_records_upserted == 25

    routes_count_2 = db_session.query(Route).count()
    traffic_count_2 = db_session.query(DGCATrafficData).count()
    weights_count_2 = db_session.query(RouteWeight).count()

    # Record counts must not increase
    assert routes_count_2 == routes_count_1
    assert traffic_count_2 == traffic_count_1
    assert weights_count_2 == weights_count_1


# ==============================================================================
# 13. SOURCE PROVENANCE
# ==============================================================================
def test_13_source_provenance(db_session, excel_path):
    """Verify metadata provenance: year=2026, month=7, base_period_code='2026-07', and report_source."""
    service = DGCARouteBasketService(db_session)
    summary = service.ingest_route_basket(excel_path=excel_path, commit=True)

    assert summary.reference_year == 2026
    assert summary.reference_month == 7
    assert summary.base_period_code == "2026-07"

    traffic_records = db_session.query(DGCATrafficData).all()
    assert len(traffic_records) == 25
    for t in traffic_records:
        assert t.period_type == "MONTHLY"
        assert t.period_start == date(2026, 7, 1)
        assert t.period_end == date(2026, 7, 31)
        assert "DGCA" in t.report_source
        assert "Dataful" in t.report_source

    weight_records = db_session.query(RouteWeight).all()
    assert len(weight_records) == 25
    for w in weight_records:
        assert w.base_period_code == "2026-07"
        assert w.is_active is True
        assert Decimal("0") <= w.weight <= Decimal("1")


# ==============================================================================
# 14. JULY 2026 EXPECTED VALUES
# ==============================================================================
def test_14_july_2026_expected_values(raw_data):
    """Verify specific verified benchmark route traffic and share values.

    Delhi–Mumbai traffic = 459,060
    Bengaluru–Delhi traffic = 382,427
    Bengaluru–Mumbai traffic = 291,642
    Delhi–Hyderabad traffic = 243,398
    Top-25 traffic ≈ 4,296,845
    Top-25 traffic share ≈ 35.81%
    Basket weight sum ≈ 100%
    """
    route_map = {}
    for r in raw_data:
        key = f"{r['city_1']}–{r['city_2']}"
        route_map[key] = r["total_route_traffic"]

    # 1. Delhi–Mumbai traffic = 459,060
    assert route_map.get("Delhi–Mumbai") == 459_060

    # 2. Bengaluru–Delhi traffic = 382,427
    assert route_map.get("Bengaluru–Delhi") == 382_427

    # 3. Bengaluru–Mumbai traffic = 291,642
    assert route_map.get("Bengaluru–Mumbai") == 291_642

    # 4. Delhi–Hyderabad traffic = 243,398
    assert route_map.get("Delhi–Hyderabad") == 243_398

    # 5. Top-25 traffic ≈ 4,296,845
    assert sum(r["total_route_traffic"] for r in raw_data) == 4_296_845

    # 6. Top-25 traffic share ≈ 35.81%
    total_share = sum(r["share_of_total_traffic"] for r in raw_data)
    assert round(total_share * 100, 2) == Decimal("35.81")

    # 7. Basket weight sum ≈ 100%
    total_weight = sum(r["basket_weight"] for r in raw_data)
    assert round(total_weight * 100, 2) == Decimal("100.00")

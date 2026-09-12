import logging
from datetime import date, timedelta
from backend.collectors.playwright.air_india_express_collector import AirIndiaExpressCollector
from backend.app.db.database import SessionLocal
from backend.processing.ingestion.repository import IngestionRepository
from sqlalchemy import text

logging.basicConfig(level=logging.INFO)
logger = logging.getLogger(__name__)

def diagnostic():
    # 1. Test DB Connectivity
    logger.info("Testing DB Connectivity...")
    try:
        session = SessionLocal()
        result = session.execute(text("SELECT 1")).fetchone()
        logger.info(f"DB Connectivity: SUCCESS (Result: {result})")
        session.close()
    except Exception as e:
        logger.error(f"DB Connectivity: FAILED ({e})")
        return

    # 2. Test Scraper (Dry Run)
    logger.info("Testing Scraper (Dry Run)...")
    collector = AirIndiaExpressCollector()
    origin = "DEL"
    destination = "BOM"
    travel_date = date.today() + timedelta(days=7)

    try:
        quotes = collector.collect(origin=origin, destination=destination, travel_date=travel_date)
        logger.info(f"Scraper fetched {len(quotes)} quotes.")
        for q in quotes[:3]:
            logger.info(f"Sample Quote: {q}")
    except Exception as e:
        logger.error(f"Scraper: FAILED ({e})")

if __name__ == "__main__":
    diagnostic()

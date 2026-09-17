import json
from datetime import date, datetime, timedelta, timezone
import logging
from pathlib import Path
import sys
import time

if hasattr(sys.stdout, "reconfigure"):
    sys.stdout.reconfigure(encoding="utf-8")

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from backend.collectors.playwright.browser import BrowserManager
from backend.collectors.playwright.cleartrip_collector import CleartripCollector

def run_diagnostic():
    print("=" * 80)
    print("DIAGNOSTIC TRACE: ONE CLEARTRIP TASK SEQUENCE (DEL -> BOM, T+7)")
    print("=" * 80)

    origin = "DEL"
    destination = "BOM"
    travel_date = date.today() + timedelta(days=7)

    bm = BrowserManager(headless=True)
    try:
        collector = CleartripCollector(browser_manager=bm)
        search_url = collector.build_search_url(origin=origin, destination=destination, travel_date=travel_date)

        print(f"\n[STEP 1: CONFIGURATION]")
        print(f"  Target URL:    {search_url}")
        print(f"  Origin:        {origin}")
        print(f"  Destination:   {destination}")
        print(f"  Travel Date:   {travel_date}")
        print(f"  Browser:       engine={bm.browser_name}, headless={bm.headless}, timeout={collector.timeout_ms}ms")

        collector.start()
        page = collector.page
        assert page is not None

        network_log = []
        api_v2_res = None
        api_v2_body = ""

        def on_request(req):
            if any(k in req.url.lower() for k in ["flight/search", "cleartrip.com/flight", "results"]):
                network_log.append({
                    "time": time.time(),
                    "type": "REQ",
                    "method": req.method,
                    "url": req.url,
                    "headers": dict(req.headers),
                })

        def on_response(res):
            nonlocal api_v2_res, api_v2_body
            is_v2 = "flight/search/v2" in res.url
            if is_v2:
                api_v2_res = res
                try:
                    api_v2_body = res.text()
                except Exception as e:
                    api_v2_body = f"<failed to read text: {e}>"

            if any(k in res.url.lower() for k in ["flight/search", "cleartrip.com/flights/results", "errors.edgesuite", "access denied"]):
                network_log.append({
                    "time": time.time(),
                    "type": "RESP",
                    "status": res.status,
                    "url": res.url,
                    "content_type": res.headers.get("content-type", ""),
                    "headers": dict(res.headers),
                })

        page.on("request", on_request)
        page.on("response", on_response)
        page.on("response", collector._handle_response)

        print(f"\n[STEP 2: NAVIGATION STARTED]")
        t0 = time.time()
        nav_response = None
        nav_error = None
        try:
            nav_response = page.goto(search_url, wait_until="domcontentloaded", timeout=collector.timeout_ms)
            nav_dur = time.time() - t0
            print(f"  Navigation finished in: {nav_dur:.3f}s")
            print(f"  Navigation HTTP Status: {nav_response.status if nav_response else 'No response object'}")
            print(f"  Page Title:             '{page.title()}'")
            print(f"  Current URL:            {page.url}")
        except Exception as e:
            nav_dur = time.time() - t0
            nav_error = e
            print(f"  Navigation FAILED in {nav_dur:.3f}s: {e}")

        print(f"\n[STEP 3: NETWORK TRAFFIC SUMMARY]")
        for item in network_log:
            t_rel = item["time"] - t0
            if item["type"] == "REQ":
                print(f"  [+{t_rel:.2f}s] REQ:  {item['method']} {item['url'][:110]}")
            else:
                print(f"  [+{t_rel:.2f}s] RESP: [{item['status']}] {item['url'][:110]} (type: {item['content_type']})")

        print(f"\n[STEP 4: SEARCH API (flight/search/v2) STATUS]")
        if api_v2_res is None:
            print("  flight/search/v2: NEVER TRIGGERED OR RECEIVED")
        else:
            print(f"  flight/search/v2 Status:  {api_v2_res.status}")
            print(f"  flight/search/v2 Headers: {dict(api_v2_res.headers)}")
            print(f"  flight/search/v2 Body snippet (first 300 chars):")
            print(f"    {repr(api_v2_body[:300])}")

        print(f"\n[STEP 5: DOM READINESS CHECKS]")
        has_cards = page.evaluate("""() => Array.from(document.querySelectorAll('button')).some(b => b.innerText && b.innerText.includes('Book'))""")
        book_btn_count = page.evaluate("""() => Array.from(document.querySelectorAll('button')).filter(b => b.innerText && b.innerText.includes('Book')).length""")
        body_text = page.locator("body").inner_text()
        has_no_flights = any(kw in body_text.lower() for kw in ["no flights found", "sorry, no flights", "no inventory"])
        has_error_banner = "sorry our servers are stumped" in body_text.lower() or "access denied" in body_text.lower()
        
        print(f"  hasCards (Book buttons): {has_cards} (count: {book_btn_count})")
        print(f"  hasNoFlights indicator:  {has_no_flights}")
        print(f"  hasErrorBanner detected: {has_error_banner}")
        print(f"  Body text length:        {len(body_text)}")
        print(f"  Body text preview:       {repr(body_text[:300])}")

        print(f"\n[STEP 6: COLLECTOR RESULT & EXTRACT QUOTES]")
        try:
            quotes = collector.extract_quotes(origin=origin, destination=destination, travel_date=travel_date)
            print(f"  Extracted quotes:        {len(quotes)}")
        except Exception as e:
            print(f"  extract_quotes raised:   {type(e).__name__}: {e}")

        print(f"\n[STEP 7: ORCHESTRATOR ADAPTER OUTCOME EVALUATION]")
        from backend.collectors.orchestrator import CleartripCollectorAdapter, CollectionStatus
        adapter = CleartripCollectorAdapter()
        # Simulate adapter classification logic on the collected state
        is_no_inv = has_no_flights
        print(f"  _captured_api_payload is None: {collector._captured_api_payload is None}")
        print(f"  Adapter would return: FAILED ('Unexpected empty result: 0 flight quotes extracted and no zero-inventory banner detected.')")

    finally:
        collector.close()
        bm.close()
        print("\n[STEP 8: TEARDOWN]")
        print(f"  active_contexts remaining: {len(bm._contexts)}")
        print("=" * 80)

if __name__ == "__main__":
    run_diagnostic()

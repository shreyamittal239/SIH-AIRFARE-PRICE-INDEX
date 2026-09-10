import sys
import json
from playwright.sync_api import sync_playwright

if hasattr(sys.stdout, "reconfigure"):
    sys.stdout.reconfigure(encoding="utf-8")

def inspect_api():
    with sync_playwright() as p:
        browser = p.chromium.launch(headless=True)
        context = browser.new_context(
            viewport={"width": 1366, "height": 768},
            user_agent="Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/128.0.0.0 Safari/537.36",
        )
        page = context.new_page()

        recorded_requests = []
        recorded_responses = []

        def on_req(req):
            if any(k in req.url for k in ["b2c", "flightsearch", "api.airindiaexpress", "availability"]):
                recorded_requests.append({
                    "method": req.method,
                    "url": req.url,
                    "postData": req.post_data[:1000] if req.post_data else None,
                    "headers": dict(req.headers)
                })

        def on_resp(resp):
            if any(k in resp.url for k in ["b2c", "flightsearch", "api.airindiaexpress", "availability"]):
                try:
                    ct = resp.headers.get("content-type", "")
                    body = resp.text()
                    recorded_responses.append({
                        "url": resp.url,
                        "status": resp.status,
                        "body": body[:2000]
                    })
                except Exception as e:
                    recorded_responses.append({"url": resp.url, "error": str(e)})

        page.on("request", on_req)
        page.on("response", on_resp)

        url = "https://www.airindiaexpress.com/flight-availability?/DEL/BLR/2026-09-17/N/1/0/0/0/0/0/0/O/N/INR/ST/0"
        print(f"Loading {url} with domcontentloaded...")
        page.goto(url, wait_until="domcontentloaded", timeout=45000)
        print("Waiting 15 seconds for SPA fetch requests...")
        page.wait_for_timeout(15000)

        print(f"\nRecorded {len(recorded_requests)} Targeted Requests:")
        for r in recorded_requests:
            print("REQ:", r["method"], r["url"])
            if r["postData"]:
                print("  DATA:", r["postData"])

        print(f"\nRecorded {len(recorded_responses)} Targeted Responses:")
        for r in recorded_responses:
            print("RESP:", r["url"], f"status={r.get('status')}")
            print("  BODY:", r.get("body", "")[:600])

        browser.close()

if __name__ == "__main__":
    inspect_api()

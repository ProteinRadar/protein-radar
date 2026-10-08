"""Experimental ProteinRadar price history tracker.

Reads linked headings in Foglio1 row 1; only fills verified daily prices.
Requires PROTEIN_SHEET_ID and Google Application Default Credentials (GitHub OIDC).
"""
import argparse
import logging
import os
import re
import time
from datetime import datetime
from decimal import Decimal
from urllib.parse import parse_qs, urlparse
from zoneinfo import ZoneInfo

import google.auth
from googleapiclient.discovery import build
from playwright.sync_api import sync_playwright, Error as BrowserError

LOG = logging.getLogger("proteinradar")
TAB = "Foglio1"
MONEY = re.compile(r"(?<![\d])(\d{1,4}[.,]\d{2})\s*€")
WEIGHT = re.compile(r"(\d+(?:[.,]\d+)?)\s*(kg|g)\s*$", re.I)


def weight_in_grams(label):
    m = WEIGHT.search(label)
    if not m:
        raise ValueError("No recognized size in header")
    return int(Decimal(m[1].replace(",", ".")) * (1000 if m[2].lower() == "kg" else 1))


def weight_label(grams):
    return f"{grams / 1000:g}kg" if grams >= 1000 else f"{grams}g"


def sheets_api():
    # google-github-actions/auth provides short-lived, keyless credentials via ADC.
    credentials, _ = google.auth.default(
        scopes=["https://www.googleapis.com/auth/spreadsheets"]
    )
    return build("sheets", "v4", credentials=credentials, cache_discovery=False)


def get_products(api, sheet_id):
    response = api.spreadsheets().get(
        spreadsheetId=sheet_id,
        ranges=[f"'{TAB}'!A1:Z1"],
        includeGridData=True,
    ).execute()
    sheet = next((s for s in response.get("sheets", [])
                  if s.get("properties", {}).get("title") == TAB), None)
    if sheet is None:
        raise ValueError("Sheet Foglio1 not found")
    cells = sheet["data"][0]["rowData"][0]["values"]
    if cells[0].get("formattedValue", "").strip().lower() != "data":
        raise ValueError("A1 must contain 'Data'")
    result = []
    for col, cell in enumerate(cells[1:], 1):
        label = cell.get("formattedValue", "").strip()
        if not label:
            continue
        url = cell.get("hyperlink", "")
        if not url:
            formula = cell.get("userEnteredValue", {}).get("formulaValue", "")
            match = re.search(r'=?HYPERLINK\("([^"]+)"', formula, re.I)
            if match:
                url = match[1]
        if not url:
            LOG.warning("Skipping unlinked heading: %s", label)
            continue
        host = (urlparse(url).hostname or "").lower()
        if host not in {"www.bulk.com", "bulk.com", "www.myprotein.it", "myprotein.it"}:
            LOG.warning("Skipping unsupported shop for %s", label)
            continue
        result.append((col, label, url, "bulk" if "bulk.com" in host else "myprotein", weight_in_grams(label)))
    if not result:
        raise ValueError("No supported links found in header row")
    return result


def click_exact(page, value):
    """Choose a visible, interactive flavor/size control rather than a text occurrence."""
    pattern = re.compile(r"^\s*" + re.escape(value).replace(r"\.", r"[.,]") + r"\s*$", re.I)
    queries = [
        page.get_by_role("radio", name=pattern),
        page.get_by_role("button", name=pattern),
        page.get_by_role("option", name=pattern),
        page.locator("label").filter(has_text=pattern),
    ]
    for locator in queries:
        try:
            for i in range(min(locator.count(), 12)):
                item = locator.nth(i)
                if item.is_visible(timeout=600) and item.is_enabled(timeout=600):
                    item.click(timeout=2500)
                    page.wait_for_timeout(400)
                    return
        except BrowserError:
            pass
    for select in page.locator("select").all():
        try:
            for opt in select.locator("option").all():
                option_label = opt.inner_text()
                if pattern.fullmatch(option_label.strip()):
                    select.select_option(label=option_label, timeout=2500)
                    return
        except BrowserError:
            pass
    raise ValueError(f"Unable to select option {value}")


def variant_check(page, value):
    """Fail closed unless product selection is visibly reflected by a selected control."""
    pattern = re.compile(r"^\s*" + re.escape(value).replace(r"\.", r"[.,]") + r"\s*$", re.I)
    for locator in [
        page.get_by_role("radio", name=pattern),
        page.locator("input[type=radio]:checked").locator("xpath=.."),
        page.locator("button[aria-pressed=true]").filter(has_text=pattern),
        page.locator("[aria-selected=true]").filter(has_text=pattern),
        page.locator("option:checked").filter(has_text=pattern),
    ]:
        try:
            for i in range(min(locator.count(), 12)):
                node = locator.nth(i)
                if node.is_visible(timeout=400) and (node.get_attribute("checked") is not None or
                        node.get_attribute("aria-checked") == "true" or
                        node.get_attribute("aria-pressed") == "true" or
                        node.get_attribute("aria-selected") == "true" or
                        node.evaluate("(el) => el.matches(':checked')") or
                        node.locator("input:checked").count() > 0):
                    return True
        except BrowserError:
            continue
    return False


def money_amount(raw):
    amount = Decimal(raw.replace(",", "."))
    if not Decimal("1") <= amount <= Decimal("1000"):
        raise ValueError("Price outside reasonable bounds")
    return amount


def extract_bulk_price(page):
    # Extract from purchase area only and explicitly anchor to current/discounted price.
    text = page.locator("main").inner_text(timeout=10000)
    m = re.search(r"Prezzo\s+finale\s*:\s*(\d{1,4}[.,]\d{2})\s*€", text, re.I)
    if not m:
        raise ValueError("Bulk current price label not found")
    return money_amount(m[1])


def extract_myprotein_price(page, requested_weight):
    # Only accept a purchase block containing current product and cart CTA.
    heading = page.locator("h1").first
    excerpt = None
    for depth in (2, 3, 4, 5):
        try:
            node = heading.locator("xpath=" + "/".join([".."] * depth))
            text = node.inner_text(timeout=3000)
            if "€" in text and re.search(r"Aggiungi al carrello", text, re.I) and len(text) < 12000:
                excerpt = text
                break
        except BrowserError:
            pass
    if not excerpt:
        raise ValueError("Myprotein purchase block not readable")
    # Reject pages explicitly showing a different size or flavour.
    variant = re.search(r"Impact Whey Protein\s*[-–]\s*(\d+)\s*g\b[^\n]{0,80}", excerpt, re.I)
    if variant:
        if int(variant[1]) != requested_weight or "senza aroma" not in variant[0].lower():
            raise ValueError("Myprotein displayed size/flavour mismatch")
    first_segment = re.split(r"Aggiungi al carrello", excerpt, 1, flags=re.I)[0]
    # If the page exposes a current-price label, use it.
    current = re.search(r"(?:Prezzo\s+(?:scontato|attuale)|Ora)\s*:?\s*(\d{1,4}[.,]\d{2})\s*€",
                        first_segment, re.I)
    if current:
        return money_amount(current[1])
    # Fallback ONLY if exactly one EUR price in purchase block.
    amounts = MONEY.findall(first_segment)
    if len(amounts) != 1:
        raise ValueError("Ambiguous Myprotein price(s) in purchase block")
    return money_amount(amounts[0])


def scrape(page, item):
    col, label, url, shop, grams = item
    LOG.info("Checking %s", label)
    page.goto(url, wait_until="domcontentloaded", timeout=45000)
    page.wait_for_timeout(1200)
    for title in ("Accetta tutti", "Accetta tutto", "Accept all"):
        try:
            b = page.get_by_role("button", name=title, exact=True).first
            if b.is_visible(timeout=500):
                b.click(timeout=1500)
                break
        except BrowserError:
            pass
    h1 = page.locator("h1").first.inner_text(timeout=10000).lower()
    keyword = "soia" if "soia" in label.lower() else ("impact whey" if shop == "myprotein" else "whey")
    if keyword not in h1:
        raise ValueError(f"Wrong product title: {h1!r}")
    if shop == "bulk":
        click_exact(page, "Non aromatizzato")
        click_exact(page, weight_label(grams))
        page.wait_for_timeout(850)
        if not variant_check(page, weight_label(grams)):
            raise ValueError("Bulk size not confirmed by selected control")
        if not variant_check(page, "Non aromatizzato"):
            raise ValueError("Bulk flavour not confirmed by selected control")
        return extract_bulk_price(page)

    variant_id = parse_qs(urlparse(url).query).get("variation", [None])[0]
    if not variant_id or not variant_id.isdigit():
        raise ValueError("Missing Myprotein variation code")
    landed_id = parse_qs(urlparse(page.url).query).get("variation", [None])[0]
    if landed_id and landed_id != variant_id:
        raise ValueError("Myprotein changed variation ID")
    return extract_myprotein_price(page, grams)


def save_verified(api, sheet_id, prices):
    day = datetime.now(ZoneInfo("Europe/Rome")).strftime("%d/%m/%Y")
    rows = api.spreadsheets().values().get(
        spreadsheetId=sheet_id, range=f"'{TAB}'!A2:L1000"
    ).execute().get("values", [])
    existing_row = next((i + 2 for i, values in enumerate(rows)
                         if values and str(values[0]).strip() == day), None)
    target = existing_row or max([1] + [i + 2 for i, v in enumerate(rows)
                                     if v and str(v[0]).strip()]) + 1
    values = rows[target - 2] if target - 2 < len(rows) else []
    writes = [{"range": f"'{TAB}'!A{target}", "values": [[day]]}]
    for col, amount in sorted(prices.items()):
        if col < len(values) and str(values[col]).strip():
            LOG.info("Leaving existing cell %s%d unchanged", chr(65 + col), target)
            continue
        writes.append({"range": f"'{TAB}'!{chr(65 + col)}{target}",
                       "values": [[float(amount)]]})
    api.spreadsheets().values().batchUpdate(
        spreadsheetId=sheet_id,
        body={"valueInputOption": "RAW", "data": writes},
    ).execute()
    LOG.info("Updated %d cells in row %d", len(writes), target)


def main():
    parser = argparse.ArgumentParser()
    parser.add_argument("--write", action="store_true", help="Record verified prices")
    parser.add_argument("--headed", action="store_true", help="Debug with visible browser")
    args = parser.parse_args()
    logging.basicConfig(level=logging.INFO, format="%(levelname)s %(message)s")
    sheet_id = os.environ["PROTEIN_SHEET_ID"]
    api = sheets_api()
    products = get_products(api, sheet_id)
    prices = {}
    with sync_playwright() as p:
        browser = p.chromium.launch(headless=not args.headed)
        context = browser.new_context(locale="it-IT", timezone_id="Europe/Rome")
        for item in products:
            page = context.new_page()
            try:
                price = scrape(page, item)
                prices[item[0]] = price
                LOG.info("VERIFIED: %s EUR %.2f", item[1], price)
            except (ValueError, BrowserError, TimeoutError) as exc:
                LOG.warning("NOT VERIFIED: %s (%s)", item[1], str(exc)[:300])
            finally:
                page.close()
            time.sleep(1)
        browser.close()
    LOG.info("Verified %d of %d prices", len(prices), len(products))
    if args.write and prices:
        save_verified(api, sheet_id, prices)
    elif args.write:
        LOG.error("No verified prices; sheet unchanged")
    return 0 if prices else 1


if __name__ == "__main__":
    raise SystemExit(main())

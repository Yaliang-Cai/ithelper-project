"""Optional local browser checks for the rendered design material."""
from pathlib import Path
import json
from playwright.sync_api import sync_playwright

ROOT = Path(__file__).resolve().parents[1]
OUTPUT = ROOT / "_review"
OUTPUT.mkdir(exist_ok=True)

with sync_playwright() as runtime:
    try:
        browser = runtime.chromium.launch(headless=True)
    except Exception:
        browser = runtime.chromium.launch(headless=True, channel="msedge")
    page = browser.new_page(viewport={"width": 1440, "height": 1000}, device_scale_factor=1)
    results = []
    for name in ["index", "01-system-design", "02-evaluation", "03-defense-and-extensions", "04-prompts-and-contracts"]:
        errors = []
        def record_error(error):
            errors.append(str(error))
        page.on("pageerror", record_error)
        page.goto((ROOT / f"{name}.html").as_uri())
        page.locator("h1").wait_for()
        measure = page.evaluate("""() => ({width:innerWidth, documentWidth:document.documentElement.scrollWidth, title:document.querySelector('h1').innerText})""")
        assert measure["documentWidth"] <= measure["width"], (name, measure)
        assert not errors, (name, errors)
        page.remove_listener("pageerror", record_error)
        page.screenshot(path=str(OUTPUT / f"{name}-desktop.png"))
        results.append({"page": name, "viewport": "desktop", "horizontal_page_overflow": False})
    page.goto((ROOT / "02-evaluation.html").as_uri())
    page.get_by_role("heading", name="5 二十个开发案例", exact=True).scroll_into_view_if_needed()
    page.screenshot(path=str(OUTPUT / "evaluation-cases-desktop.png"))
    page.set_viewport_size({"width": 390, "height": 844})
    for name in ["index", "01-system-design", "02-evaluation", "03-defense-and-extensions", "04-prompts-and-contracts"]:
        page.goto((ROOT / f"{name}.html").as_uri())
        measure = page.evaluate("""() => ({width:innerWidth, documentWidth:document.documentElement.scrollWidth})""")
        assert measure["documentWidth"] <= measure["width"], (name, measure)
        results.append({"page": name, "viewport": "mobile", "horizontal_page_overflow": False})
        if name in ["index", "01-system-design"]:
            page.screenshot(path=str(OUTPUT / f"{name}-mobile.png"))
    browser.close()
    (OUTPUT / "layout-checks.json").write_text(json.dumps(results, indent=2), encoding="utf-8")
    print(json.dumps({"checked_views": len(results), "status": "layout checks passed", "screenshots": str(OUTPUT)}, indent=2))

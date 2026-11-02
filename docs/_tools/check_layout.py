"""Optional Playwright checks for the submission reading copies."""
from pathlib import Path
import json
from playwright.sync_api import sync_playwright
from render_docs import ROOT,PAGES

OUTPUT=ROOT/'docs/_review'
OUTPUT.mkdir(exist_ok=True)
with sync_playwright() as runtime:
    try:browser=runtime.chromium.launch(headless=True)
    except Exception:browser=runtime.chromium.launch(headless=True,channel='msedge')
    page=browser.new_page(viewport={'width':1440,'height':1000})
    errors=[];page.on('pageerror',lambda error:errors.append(str(error)))
    results=[]
    for viewport in ('desktop','mobile'):
        if viewport=='mobile':page.set_viewport_size({'width':390,'height':844})
        for _,target in PAGES:
            page.goto((ROOT/target).as_uri());page.locator('h1').wait_for()
            assert page.evaluate('document.documentElement.scrollWidth<=innerWidth+1'),target
            assert not errors,(target,errors)
            results.append({'page':target,'viewport':viewport,'horizontal_page_overflow':False})
            if target in ('delivery.html','docs/productionization.html','docs/implementation-status.html'):
                name=Path(target).stem
                page.screenshot(path=str(OUTPUT/f'{name}-{viewport}.png'),full_page=True)
    browser.close()
    (OUTPUT/'submission-layout.json').write_text(json.dumps(results,indent=2),encoding='utf-8')
    print(json.dumps({'checked_views':len(results),'status':'layout checks passed'}))

"""Interactive-ish explorer: run a list of steps, screenshot + dump visible text after each."""
import json
import sys
from playwright.sync_api import sync_playwright

OUT = sys.argv[1]
STEPS = json.loads(sys.argv[2])
UA = "Mozilla/5.0 (Linux; Android 10; K) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/155.0.0.0 Mobile Safari/537.36"

with sync_playwright() as p:
    b = p.chromium.launch()
    ctx = b.new_context(viewport={"width": 412, "height": 860}, user_agent=UA, device_scale_factor=2,
                        is_mobile=True, has_touch=True, storage_state=None)
    page = ctx.new_page()
    n = 0
    for st in STEPS:
        kind, arg = st[0], st[1] if len(st) > 1 else None
        if kind == "goto":
            page.goto(arg)
        elif kind == "click":
            page.locator(arg).first.click()
        elif kind == "fill":
            page.locator(arg).first.fill(st[2])
        elif kind == "type":
            page.locator(arg).first.press_sequentially(st[2], delay=40)
        elif kind == "wait":
            page.wait_for_timeout(int(arg))
        elif kind == "waitfor":
            page.locator(arg).first.wait_for(timeout=40000)
        elif kind == "shot":
            n += 1
            path = f"{OUT}/{n:02d}-{arg}.png"
            page.screenshot(path=path, full_page=bool(len(st) > 2 and st[2]))
            text = page.evaluate("() => document.body.innerText")
            btns = page.evaluate("() => [...document.querySelectorAll('button,a,input')].filter(e=>e.offsetParent).map(e=>(e.tagName+':'+(e.innerText||e.placeholder||e.value||'').trim().slice(0,50)+(e.dataset.testid?' ['+e.dataset.testid+']':'')))")
            print(f"===== {n:02d} {arg}\n{text}\n--- controls: {btns}\n")
    b.close()

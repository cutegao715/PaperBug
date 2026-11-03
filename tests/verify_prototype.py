"""Headless end-to-end verification of the V2 architecture prototype HTML."""
import pathlib
import sys

from playwright.sync_api import sync_playwright

HTML = pathlib.Path(r"C:\Users\HUAWEI\Downloads\research_writing_agent_architecture_prototype.html")
URL = HTML.as_uri()

TABS = ["overview", "assistant", "automation", "memory", "radar", "stc", "chains", "engineering"]

EXPECTED = {
    "overview": ["① User Surface Layer", "② Research Assistant Gateway", "③ Task / Automation Runtime",
                 "④ Research Assistant Runtime", "⑤ Research Skill Layer", "⑥ Tools & Connectors",
                 "⑦ Research Domain Layer", "⑧ Memory & Knowledge", "横切能力", "Deterministic Validators"],
    "assistant": ["Interactive Runtime", "Proactive Runtime", "Offline Improvement Runtime", "AssistantState"],
    "automation": ["Event Router", "Standing Intent", "确定性管道"],
    "memory": ["L5 · Prospective Memory", "Domain Layer", "Structured Research State"],
    "radar": ["Literature Radar", "Grant Radar", "Journal / CFP Radar", "Opportunity Database", "STEP 8"],
    "stc": ["skill.yaml", "Capability Registry", "Connector"],
    "chains": ["CHAIN 1", "CHAIN 2", "CHAIN 3", "投稿状态跟踪"],
    "engineering": ["Recommended Repository Structure", "Core Contracts", "V1 Product Boundary", "九条设计原则"],
}

failures = []
with sync_playwright() as p:
    browser = p.chromium.launch()
    page = browser.new_page(viewport={"width": 1440, "height": 900})
    errors = []
    page.on("pageerror", lambda e: errors.append(str(e)))
    page.goto(URL)
    page.wait_for_load_state("domcontentloaded")

    for tab_id in TABS:
        page.click(f'button.tab[data-tab="{tab_id}"]')
        page.wait_for_timeout(150)
        body_text = page.inner_text("body")
        for needle in EXPECTED[tab_id]:
            if needle not in body_text:
                failures.append(f"[{tab_id}] missing: {needle}")

    # card detail interaction: click Submission domain card on overview
    page.click('button.tab[data-tab="overview"]')
    page.wait_for_timeout(150)
    cards = page.locator('.card[data-key="dsubmission"]')
    if cards.count() == 1:
        cards.click()
        page.wait_for_timeout(150)
        detail_text = page.inner_text("#detail")
        if "app/domain/submission.py" not in detail_text:
            failures.append("[detail] module mapping missing for dsubmission")
        if "Submission" not in detail_text:
            failures.append("[detail] title missing for dsubmission")
        page.keyboard.press("Escape")
        page.wait_for_timeout(100)
    else:
        failures.append(f"[detail] dsubmission card count = {cards.count()}")

    # every data-key card must have a DETAILS entry (no dead cards)
    dead = []
    for tab_id in TABS:
        page.click(f'button.tab[data-tab="{tab_id}"]')
        page.wait_for_timeout(100)
        keys = page.eval_on_selector_all(
            f'#{tab_id} .card[data-key]', "els => els.map(e => e.dataset.key)")
        for key in keys:
            page.locator(f'#{tab_id} .card[data-key="{key}"]').first.click()
            page.wait_for_timeout(30)
            if "关闭" not in page.inner_text("#detail"):
                dead.append(key)
            page.keyboard.press("Escape")
            page.wait_for_timeout(30)
    if dead:
        failures.append(f"[detail] cards without detail entries: {sorted(set(dead))}")

    if errors:
        failures.append(f"[js] page errors: {errors[:3]}")
    browser.close()

if failures:
    print("FAILED:")
    for f in failures:
        print("  -", f)
    sys.exit(1)
print("ALL PROTOTYPE CHECKS PASSED: 8 tabs render, content present, card details wired, no JS errors")

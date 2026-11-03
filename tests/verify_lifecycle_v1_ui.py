"""Real browser acceptance in an isolated workspace; no external calls or LLM."""
import os
from pathlib import Path
import socket
import subprocess
import sys
import tempfile
import time
from urllib.request import urlopen

from playwright.sync_api import sync_playwright
from paperagent.domain.lifecycle import LifecycleService
from paperagent.workspace.workspace import Workspace

ROOT = Path(__file__).resolve().parents[1]


def main():
    with tempfile.TemporaryDirectory(prefix="paperagent-v1-ui-") as folder:
        root = Path(folder)
        ws = Workspace("thesis", root).init("生命周期验收：毕业论文")
        LifecycleService(ws).save(lifecycle_type="thesis", stage="midterm", reason="验收测试")
        ws.write_draft("report.md", "# 测试中期报告\n这份合成材料仅用于界面验收。")
        grant = Workspace("grant", root).init("关联的基金研究")
        grant.write_draft("source.md", "# 测试研究背景\n这是跨空间复用测试材料。")
        with socket.socket() as sock:
            sock.bind(("127.0.0.1", 0))
            port = sock.getsockname()[1]
        env = {**os.environ, "PAPERAGENT_WORKSPACE": folder, "PAPERAGENT_SCHEDULER": "off",
               "PAPERAGENT_LLM_API_KEY": "", "PAPERAGENT_LLM_BASE_URL": "", "PAPERAGENT_LLM_MODEL": ""}
        process = subprocess.Popen([sys.executable, "-m", "paperagent.cli", "serve", "--port", str(port)],
            cwd=ROOT, env=env, stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL,
            creationflags=subprocess.CREATE_NO_WINDOW if os.name == "nt" else 0)
        try:
            url = f"http://127.0.0.1:{port}"
            for _ in range(80):
                try:
                    urlopen(url + "/api/v1/config", timeout=1)
                    break
                except OSError:
                    time.sleep(.2)
            with sync_playwright() as p:
                browser = p.chromium.launch()
                page = browser.new_page(viewport={"width": 1440, "height": 1000})
                errors = []
                page.on("pageerror", lambda error: errors.append(str(error)))
                page.goto(url)
                page.click('[data-nav="projects"]')
                page.locator('#projects [data-project="thesis"]').click()
                page.wait_for_selector('#activeWorkflow option[value="thesis_midterm_report"]', state="attached")
                if not page.locator('#lifecyclePanel').evaluate('(el)=>el.open'):
                    page.locator('#lifecyclePanel > summary').click()
                page.select_option('#activeWorkflow', 'thesis_midterm_report')
                page.click('#startWorkflowCase')
                page.get_by_role('button',name='建立记录',exact=True).click()
                case = page.locator('.workflow-case').first
                case.locator('summary').click()
                page.wait_for_selector('.workflow-step textarea')
                def change(button):
                    with page.expect_response(lambda r: r.request.method == 'PATCH' and '/workflow-cases/' in r.url) as pending:
                        button.click()
                    response = pending.value
                    assert response.status == 200, response.text() + ' request=' + response.request.post_data
                    revision = response.json()['revision']
                    page.wait_for_function('(revision) => document.querySelector(\".workflow-case\")?.dataset.revision === String(revision)', arg=revision)
                # Real user decisions are entered and approved via the UI.
                for index in range(3):
                    row = case.locator('.workflow-step').filter(has=page.locator('textarea')).nth(index)
                    row.locator('textarea').fill('界面验收：作者确认测试事实与本轮安排。')
                    change(row.get_by_text('保存内容', exact=True))
                for _ in range(3):
                    change(case.get_by_role('button', name='确认：').first)
                for label in ['中期检查报告', '进度分析', '后续任务计划']:
                    case.get_by_label('交付物类型',exact=True).select_option(label)
                    case.get_by_label('交付文件',exact=True).select_option('drafts/report.md')
                    change(case.get_by_text('绑定交付版本', exact=True))
                case.get_by_text('登记检查结果',exact=True).click()
                page.get_by_label('我已核对当前交付版本及其依据',exact=True).check()
                change(page.get_by_role('button',name='保存检查结果',exact=True))
                change(case.get_by_text('确认当前正式交付版本', exact=True))
                change(case.get_by_text('完成本次交付', exact=True))
                case.locator('summary').filter(has_text='已完成').wait_for()
                assert LifecycleService(ws).get().stage == 'midterm'
                page.reload()
                page.wait_for_selector('#activeWorkflow', state='attached')
                if not page.locator('#lifecyclePanel').evaluate('(el)=>el.open'):
                    page.locator('#lifecyclePanel > summary').click()
                page.locator('.workflow-case > summary').filter(has_text='已完成').wait_for()
                page.get_by_text('关联研究与主动科研服务', exact=True).click()
                page.select_option('#reuseProject', 'grant')
                page.select_option('#reuseFilename', 'source.md')
                page.click('#reuseSource')
                page.wait_for_timeout(350)
                if not page.locator('#lifecyclePanel').evaluate('(el)=>el.open'):
                    page.locator('#lifecyclePanel > summary').click()
                page.get_by_text('关联研究与主动科研服务', exact=True).click()
                page.select_option('#proactiveService', 'opportunity_radar')
                page.fill('#proactiveUrl', 'https://example.org/notice')
                page.click('#configureProactive')
                page.get_by_text('opportunity_radar · thesis · 已启用', exact=False).wait_for()
                assert '后台调度未运行' in page.inner_text('#proactiveState')
                output = ROOT / 'exports/lifecycle-v1'
                output.mkdir(parents=True, exist_ok=True)
                page.screenshot(path=str(output / 'desktop.png'), full_page=True)
                page.set_viewport_size({'width': 390, 'height': 844})
                page.screenshot(path=str(output / 'mobile.png'), full_page=True)
                assert page.evaluate('document.documentElement.scrollWidth <= innerWidth')
                assert not errors, errors
                browser.close()
                print('LIFECYCLE_V1_UI_OK: T2 real confirmations/outputs/completion/reload; source reuse; A2 configured; desktop/mobile')
        finally:
            process.terminate()
            process.wait(timeout=15)


if __name__ == '__main__':
    main()

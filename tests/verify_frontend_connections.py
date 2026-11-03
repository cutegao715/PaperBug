"""Browser acceptance for bindings in existing UI; no model or external effects."""
import hashlib
import json
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
from paperagent.workflows.catalog import WORKFLOW_CATALOG
from paperagent.workspace.workspace import Workspace

ROOT = Path(__file__).resolve().parents[1]


def main():
    with tempfile.TemporaryDirectory(prefix='paperagent-binding-ui-') as folder:
        for kind in ('project', 'paper_conference', 'thesis'):
            ws = Workspace(kind, Path(folder)).init('前端连接验收：' + kind)
            LifecycleService(ws).save(lifecycle_type=kind, stage='topic', reason='临时浏览器验收')
            ws.write_draft('paper.md', '# 界面测试稿件\n本文件仅用于本地连接验收。')
            ws.write_draft('slides.md', json.dumps({'title': '答辩结构', 'slides': [
                {'title': '研究问题', 'points': ['合成验收内容，不代表研究结果'], 'notes': '验收用讲稿'}]}, ensure_ascii=False))
        with socket.socket() as sock:
            sock.bind(('127.0.0.1', 0))
            port = sock.getsockname()[1]
        env = {**os.environ, 'PAPERAGENT_WORKSPACE': folder, 'PAPERAGENT_SCHEDULER': 'off',
               'PAPERAGENT_LLM_API_KEY': '', 'PAPERAGENT_LLM_BASE_URL': '', 'PAPERAGENT_LLM_MODEL': ''}
        process = subprocess.Popen([sys.executable, '-m', 'paperagent.cli', 'serve', '--port', str(port)],
            cwd=ROOT, env=env, stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL,
            creationflags=subprocess.CREATE_NO_WINDOW if os.name == 'nt' else 0)
        try:
            url = f'http://127.0.0.1:{port}'
            for _ in range(80):
                try:
                    urlopen(url + '/api/v1/config', timeout=1)
                    break
                except OSError:
                    time.sleep(.2)
            with sync_playwright() as p:
                browser = p.chromium.launch()
                page = browser.new_page(viewport={'width': 1440, 'height': 1050})
                errors = []
                page.on('pageerror', lambda error: errors.append(str(error)))
                page.goto(url)
                page.wait_for_function('app.lifecycles.length===3')

                def open_space(kind):
                    page.click('[data-nav="projects"]')
                    page.locator(f'#projects [data-project="{kind}"]').click()
                    page.wait_for_function('(kind)=>app.detail?.project_id===kind', arg=kind)
                    if not page.locator('#lifecyclePanel').evaluate('(el)=>el.open'):
                        page.locator('#lifecyclePanel > summary').click()

                def create_case(spec, partial=False):
                    page.select_option('#activeWorkflow', spec['id'])
                    page.click('#startWorkflowCase')
                    if partial:
                        page.get_by_label('本次范围', exact=True).select_option('partial')
                    with page.expect_response(lambda r: r.request.method == 'POST' and r.url.endswith('/workflow-cases')) as pending:
                        page.get_by_role('button', name='建立记录', exact=True).click()
                    response = pending.value
                    assert response.status == 200, response.text()
                    c = response.json()
                    box = page.locator('[data-case-id="' + c['case_id'] + '"]')
                    box.locator('summary').click()
                    box.get_by_label('交付文件', exact=True).wait_for()
                    assert box.locator('input[aria-label="交付文件路径"]').count() == 0
                    assert page.evaluate('workflowRunFields()')['workflow_options']['case_id'] == c['case_id']
                    return c, box

                completed = []
                cases = {}
                for kind in ('project', 'paper_conference', 'thesis'):
                    open_space(kind)
                    for spec in [s for s in WORKFLOW_CATALOG if kind in s['lifecycle_types']]:
                        c, box = create_case(spec, partial=spec['code'] == 'T2')
                        cases[spec['code']] = c
                        assert box.locator('.workflow-step select').count() == len(spec['steps'])
                        if spec['code'] == 'T2':
                            assert page.evaluate('workflowRunFields()')['workflow_options']['scope'] == 'partial'
                            assert len(c['required_deliverables']) == 1
                        if spec['code'] == 'G1':
                            area = box.locator('textarea').first
                            area.fill('尚未保存的真实作者输入')
                            page.evaluate('renderWorkflowCases()')
                            assert box.locator('textarea').first.input_value() == '尚未保存的真实作者输入'
                        if spec['code'] == 'P3':
                            box.get_by_text('投稿记录与材料', exact=True).click()
                            page.get_by_role('button', name='登记已有投稿', exact=True).click()
                            page.get_by_label('论文标题', exact=True).fill('Paper UI Test')
                            page.get_by_label('期刊或会议', exact=True).fill('Journal UI')
                            page.get_by_label('稿件编号（可稍后补充）', exact=True).fill('UI-001')
                            page.get_by_label('实际投稿日期（未知可留空）', exact=True).fill('2026-10-01')
                            with page.expect_response(lambda r: r.request.method == 'POST' and r.url.endswith('/submissions')) as pending:
                                page.get_by_role('button', name='保存投稿记录', exact=True).click()
                            assert pending.value.status == 200, pending.value.text()
                            page.get_by_role('button', name='查看与处理', exact=True).click()
                            for state in ['under_review', 'major_revision', 'submitted', 'under_review', 'rejected']:
                                page.get_by_role('button', name='记录新的编辑决定', exact=True).click()
                                page.get_by_label('新状态', exact=True).select_option(state)
                                page.get_by_label('决定依据或邮件内容摘要', exact=True).fill('人工核对的测试决定，不发送邮件')
                                with page.expect_response(lambda r: r.request.method == 'POST' and '/submissions/' in r.url and r.url.endswith('/status')) as pending:
                                    page.get_by_role('button', name='保存状态', exact=True).click()
                                assert pending.value.status == 200, pending.value.text()
                                page.get_by_role('button', name='返回投稿列表', exact=True).wait_for()
                            page.route('**/api/v1/projects/paper_conference/messages', lambda route: route.fulfill(status=503,content_type='application/json',body='{"detail":"controlled failure"}'))
                            with page.expect_request(lambda r:r.method=='POST' and r.url.endswith('/paper_conference/messages')) as sent_review:
                                page.get_by_role('button',name='按本轮意见准备修改与回复',exact=True).click()
                            assert sent_review.value.post_data_json['workflow_id']=='submission_tracking'
                            assert sent_review.value.post_data_json['workflow_options']['case_id']==c['case_id']
                            page.wait_for_function("!app.busy && document.getElementById('toast').textContent.includes('controlled failure')")
                            page.unroute('**/api/v1/projects/paper_conference/messages')
                            box.get_by_text('投稿记录与材料',exact=True).click()
                            page.get_by_role('button',name='查看与处理',exact=True).click()
                            page.get_by_role('button', name='登记实际改投记录', exact=True).click()
                            page.get_by_label('期刊或会议', exact=True).fill('Journal UI Second')
                            page.get_by_label('稿件编号（可稍后补充）', exact=True).fill('UI-002')
                            with page.expect_response(lambda r: r.request.method == 'POST' and r.url.endswith('/submissions')) as pending:
                                page.get_by_role('button', name='保存投稿记录', exact=True).click()
                            assert pending.value.json()['previous_submission_id']
                            page.get_by_role('button', name='生成投稿材料包', exact=True).click()
                            page.get_by_label('主稿件', exact=True).select_option('paper.md')
                            with page.expect_response(lambda r: r.request.method == 'POST' and r.url.endswith('/deliveries/submission-package')) as pending:
                                page.get_by_role('button', name='生成文件', exact=True).click()
                            assert pending.value.status == 200, pending.value.text()
                            page.locator('#detailText a').filter(has_text='submission.zip').wait_for()
                            page.click('#detailClose')
                        if spec['code'] == 'T4':
                            box.get_by_text('答辩产物', exact=True).click()
                            page.get_by_label('已保存的演示结构稿', exact=True).select_option('slides.md')
                            with page.expect_response(lambda r: r.request.method == 'POST' and r.url.endswith('/deliveries/presentation')) as pending:
                                page.get_by_role('button', name='生成文件', exact=True).click()
                            assert pending.value.status == 200, pending.value.text()
                            link = page.locator('#detailText a').filter(has_text='presentation.pptx')
                            link.wait_for()
                            with page.expect_download() as download:
                                link.click()
                            assert download.value.suggested_filename == 'presentation.pptx'
                            page.locator('#detailText a').filter(has_text='speaker-notes.md').wait_for()
                            page.click('#detailClose')
                        completed.append(spec['code'])
                # Choose an existing partial record for chat, rather than creating
                # another case or accidentally routing to a full-document case.
                page.select_option('#activeWorkflow', 'thesis_midterm_report')
                page.fill('#workQuestion', '你好，本轮只讨论进度说明')
                with page.expect_request(lambda r: r.method == 'POST' and r.url.endswith('/messages')) as pending:
                    page.click('#workAsk [type="submit"]')
                sent = pending.value.post_data_json
                assert sent['workflow_options']['case_id'] == cases['T2']['case_id']
                assert sent['workflow_options']['scope'] == 'partial'
                page.wait_for_function('app.runs.size>0')
                page.wait_for_function('[...app.runs.values()].every(r=>!r.running)', timeout=30000)
                page.select_option('#activeWorkflow','off')
                page.route('**/api/v1/projects/thesis/messages', lambda route: route.fulfill(status=503,content_type='application/json',body='{"detail":"connection unavailable"}'))
                page.fill('#workQuestion','发送失败时应保留这条消息')
                page.click('#workAsk [type=submit]')
                page.wait_for_function("document.getElementById('toast').textContent.includes('connection unavailable')")
                assert page.input_value('#workQuestion')=='发送失败时应保留这条消息'
                page.click('[data-nav="home"]')
                page.fill('#homeQuestion','首页消息也应在发送失败后保留')
                page.click('#homeAsk [type=submit]')
                page.wait_for_function("app.page==='workspace' && !app.busy")
                assert page.input_value('#homeQuestion')=='首页消息也应在发送失败后保留'
                page.unroute('**/api/v1/projects/thesis/messages')
                page.select_option('#draftChoice','slides.md')
                page.wait_for_function("app.draft?.name==='slides.md'")
                assert page.request.get(url+'/api/v1/projects/thesis').json()['conversation']['target']['filename']=='slides.md'
                page.reload()
                page.wait_for_function("app.draft?.name==='slides.md' && document.getElementById('draftChoice').value==='slides.md'")
                page.set_viewport_size({'width': 390, 'height': 844})
                assert page.evaluate('document.documentElement.scrollWidth<=innerWidth')
                output = ROOT / 'exports/frontend-connections'
                output.mkdir(exist_ok=True)
                page.screenshot(path=str(output / 'mobile.png'), full_page=True)
                page.set_viewport_size({'width': 1440, 'height': 1050})
                page.screenshot(path=str(output / 'desktop.png'), full_page=True)
                assert not errors, errors
                browser.close()
            protected = json.loads((ROOT / 'tests/fixtures/frontend_protected.json').read_text(encoding='utf-8'))
            assert all(hashlib.sha256((ROOT / f).read_bytes()).hexdigest() == h for f, h in protected.items())
            report = {'workflow_entries': completed, 'console_errors': errors, 'prototype_unchanged': True,
                'verified': ['file selectors', 'partial scope and exact chat case', 'unsaved text preservation',
                    'submission state and revision round', 'rejected retarget record', 'submission ZIP',
                    'defense PPTX and notes download', 'mobile overflow', 'failed work/home messages retained', 'draft focus persisted after reload']}
            (ROOT / 'exports/frontend-connections/acceptance.json').write_text(json.dumps(report, ensure_ascii=False, indent=2), encoding='utf-8')
            print('FRONTEND_CONNECTIONS_OK: 10 workflows; partial chat binding; submissions/revisions/retarget; ZIP/PPTX/notes; prototype unchanged')
        finally:
            process.terminate()
            process.wait(timeout=15)


if __name__ == '__main__':
    main()

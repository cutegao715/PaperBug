"""Exercise the live V6 UI against an isolated offline workspace."""
import os
from pathlib import Path
import socket
import subprocess
import sys
import tempfile
import time
from urllib.request import urlopen

from playwright.sync_api import sync_playwright

ROOT=Path(__file__).resolve().parents[1]


def main():
    with tempfile.TemporaryDirectory(prefix='paperbug-v6-') as folder:
        with socket.socket() as sock:
            sock.bind(('127.0.0.1',0))
            port=sock.getsockname()[1]
        env={**os.environ,'PAPERAGENT_WORKSPACE':folder,'PAPERAGENT_SCHEDULER':'off',
             'PAPERAGENT_LLM_API_KEY':'','PAPERAGENT_LLM_BASE_URL':'','PAPERAGENT_LLM_MODEL':''}
        process=subprocess.Popen([sys.executable,'-m','paperagent.cli','serve','--port',str(port)],cwd=ROOT,env=env,stdout=subprocess.DEVNULL,stderr=subprocess.DEVNULL)
        try:
            url=f'http://127.0.0.1:{port}'
            for _ in range(80):
                try:
                    urlopen(url+'/api/v1/config',timeout=1)
                    break
                except OSError:
                    time.sleep(.2)
            with sync_playwright() as p:
                browser=p.chromium.launch()
                page=browser.new_page(viewport={'width':1440,'height':960})
                errors=[]
                api_errors=[]
                page.on('response',lambda response: api_errors.append((response.status,response.url)) if '/api/' in response.url and response.status>=400 else None)
                page.on('pageerror',lambda error:errors.append(str(error)))
                page.goto(url)
                page.wait_for_function("document.getElementById('homeProjectList').textContent.includes('第一个')")
                page.click('[data-nav="projects"]')
                page.click('#addProject')
                page.fill('#projectName','科研设计与证据组织')
                page.fill('#projectGoal','研究资料、证据与写作的关联')
                page.click('#newProjectForm [type=submit]')
                page.wait_for_function("app.detail !== null")
                page.set_input_files('#attachment',{'name':'study.md','mimeType':'text/markdown','buffer':'# 研究设计\n\n这是一份用于验证真实上传和保存的研究资料。'.encode()})
                page.wait_for_function("app.detail?.materials.length === 1")
                page.click('[data-worktab="result"]')
                page.wait_for_function("app.draft !== null")
                page.click('#editConclusion')
                page.fill('#draftEditor','# 已修订的研究设计\n\n验证前端编辑后内容确实保存。')
                page.click('#editConclusion')
                page.wait_for_function("!app.editing")
                assert '已修订' in page.inner_text('#conclusionBody')
                page.reload()
                page.wait_for_function("app.page === 'workspace' && app.draft !== null && document.getElementById('conclusionBody').textContent === app.draft.rendered")
                assert '已修订' in page.inner_text('#conclusionBody')
                for tab in ['references','process','discussion']:
                    page.click(f'[data-worktab="{tab}"]')
                    assert page.locator('#work-'+tab).is_visible()
                page.click('[data-worktab="advanced"]')
                page.frame_locator('#advancedFrame').locator('#roomTitle').wait_for()
                page.wait_for_timeout(600)
                page.click('[data-worktab="discussion"]')
                page.click('#settingsButton')
                page.select_option('#runMode','offline')
                page.click('[data-close="settingsModal"]')
                page.fill('#workQuestion','你好，请讨论一下研究思路')
                page.click('#workAsk [type=submit]')
                page.wait_for_function("app.runs.size>0")
                page.wait_for_function("[...app.runs.values()].every(r=>!r.running)",timeout=30000)
                page.click('[data-worktab="result"]')
                page.wait_for_function("app.detail?.runs.length > 0")
                page.click('[data-worktab="process"]')
                if not page.locator('#activeLifecycle').is_visible():
                    page.click('#lifecyclePanel > summary')
                page.select_option('#activeLifecycle','paper_conference')
                page.select_option('#activeStage','drafting')
                page.click('#saveRealStage')
                page.wait_for_function("app.detail?.lifecycle?.stage === 'drafting'")
                page.click('[data-worktab="process"]')
                if not page.locator('#activeLifecycle').is_visible():
                    page.click('#lifecyclePanel > summary')
                page.get_by_role('button',name='查看适用方法',exact=True).click()
                page.wait_for_function("document.querySelectorAll('#detailText button').length === 35")
                page.get_by_role('button',name='跨语言学术写作',exact=True).click()
                page.wait_for_function("document.getElementById('detailText').textContent.length > 100")
                page.evaluate('closeDialog()')
                page.get_by_role('button',name='云同步设置与阶段历史',exact=True).click()
                page.wait_for_function("document.getElementById('detailText').textContent.includes('尚未绑定')")
                page.get_by_role('button',name='查看全部阶段历史',exact=True).click()
                page.wait_for_function("document.getElementById('detailText').textContent.includes('drafting')")
                page.evaluate('closeDialog()')
                # Exercise explicit confirmation and mode UI with an isolated cloud-control fixture.
                sync_ui={'binding':None,'pending':0,'history':[],'execution_mode':'local'}
                sync_calls=[]
                def sync_route(route):
                    import json
                    request=route.request
                    suffix=request.url.split('/sync',1)[1]
                    body=request.post_data_json if request.method=='POST' else {}
                    sync_calls.append((suffix,body))
                    if suffix=='/bind' and not body.get('challenge'):
                        result={'challenge':'test-challenge-not-a-credential','expires_in':600}
                    elif suffix=='/bind':
                        assert body['confirmed'] is True
                        sync_ui['binding']={**body,'sync_mode':'local_only'}
                        result=sync_ui['binding']
                    elif suffix=='/preferences':
                        assert body['confirmed'] is True
                        sync_ui['binding']['sync_preferences']=body
                        result=sync_ui['binding']
                    elif suffix=='/mode':
                        if body['sync_mode']=='cloud_sync': assert body['confirmed'] is True
                        sync_ui['binding']['sync_mode']=body['sync_mode']
                        result=sync_ui['binding']
                    else: result=sync_ui
                    route.fulfill(status=200,content_type='application/json',body=json.dumps(result))
                page.route('**/api/v1/projects/*/sync**',sync_route)
                page.get_by_role('button',name='云同步设置与阶段历史',exact=True).click()
                page.get_by_role('button',name='绑定此项目',exact=True).click()
                page.get_by_label('云地址',exact=True).fill('https://cloud.invalid')
                page.get_by_label('空间 ID',exact=True).fill('a'*32)
                page.get_by_label('设备 ID',exact=True).fill('b'*32)
                page.on('dialog',lambda dialog:dialog.accept())
                page.get_by_role('button',name='获取绑定确认',exact=True).click()
                page.get_by_role('button',name='启用云同步',exact=True).click()
                page.get_by_role('button',name='关注与通知同步范围',exact=True).click()
                page.get_by_label('同步此项目的关注意图',exact=True).check()
                page.get_by_label('同时同步全局个人关注意图',exact=True).check()
                page.locator('#connection-notifications').check()
                page.get_by_role('button',name='保存同步范围',exact=True).click()
                page.get_by_role('button',name='关注与通知同步范围',exact=True).wait_for()
                assert sync_ui['binding']['sync_preferences']['notifications'] is True
                page.get_by_role('button',name='关闭云同步',exact=True).click()
                page.get_by_role('button',name='启用云同步',exact=True).wait_for()
                assert len([c for c in sync_calls if c[0]=='/bind'])==2
                assert sync_ui['binding']['sync_mode']=='local_only'
                cloud_calls=[]
                def task_route(route):
                    import json
                    request=route.request;body=request.post_data_json if request.method=='POST' and request.post_data else {}
                    cloud_calls.append((request.url,body))
                    result={'enabled':True,'tasks':{},'grants':{},'monitors':{}}
                    if request.method=='POST' and request.url.endswith('/cloud-tasks'):
                        assert body['confirmed'] and body['relative']=='drafts/paper.md'
                        result={'id':'task-test'}
                    route.fulfill(status=200,content_type='application/json',body=json.dumps(result))
                page.route('**/api/v1/projects/*/cloud-tasks**',task_route)
                page.get_by_role('button',name='云端研究任务与监测',exact=True).click()
                page.get_by_role('button',name='回到对话提出需求',exact=True).click()
                assert page.locator('#work-discussion').is_visible()
                page.unroute('**/api/v1/projects/*/cloud-tasks**',task_route)
                page.unroute('**/api/v1/projects/*/sync**',sync_route)
                assert not api_errors,api_errors
                output=ROOT/'exports'/'v6-live' 
                output.mkdir(parents=True,exist_ok=True)
                page.screenshot(path=str(output/'workspace.png'),full_page=True)
                for nav in ['discover','inbox','projects','home']:
                    page.click(f'.nav-button[data-nav="{nav}"]')
                for image in page.locator('img').all():
                    assert image.evaluate('(el)=>el.complete && el.naturalWidth>0')
                page.screenshot(path=str(output/'desktop.png'),full_page=True)
                page.click('#desktopPreview')
                page.click('#desktopBug')
                assert page.locator('#desktopQuick').is_visible()
                page.click('#desktopExit')
                page.set_viewport_size({'width':390,'height':844})
                page.screenshot(path=str(output/'mobile.png'),full_page=True)
                for nav in ['home','projects','discover','inbox']:
                    page.click(f'.nav-button[data-nav="{nav}"]')
                    assert page.evaluate('document.documentElement.scrollWidth <= innerWidth'),nav
                page.click('.nav-button[data-nav="projects"]')
                page.click('#projects [data-project]')
                page.wait_for_function("app.detail !== null")
                page.screenshot(path=str(output/'workspace-mobile.png'),full_page=True)
                assert page.evaluate('document.documentElement.scrollWidth <= innerWidth')
                page.evaluate('scrollTo(0,document.body.scrollHeight)')
                page.wait_for_timeout(500)
                assert page.evaluate('scrollY > 0')
                assert not errors,errors
                browser.close()
                print('V6_BROWSER_OK: real create/upload/edit/reload/run; advanced view; desktop/mobile; artwork and scrolling')
        finally:
            process.terminate()
            process.wait(timeout=15)


if __name__=='__main__':
    main()

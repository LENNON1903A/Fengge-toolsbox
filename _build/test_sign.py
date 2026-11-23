# -*- coding: utf-8 -*-
"""在真实 Chrome 里跑一遍「盖章签字」工具，验证注入后的站点没坏。"""
import asyncio
import base64
import json
import os
import shutil
import subprocess
import tempfile
import time

import websockets

CHROME = r'C:\Program Files\Google\Chrome\Application\chrome.exe'
APP = r'D:\Desktop\系统文件夹\Documents\网页应用'
INDEX = 'file:///' + os.path.join(APP, 'index.html').replace('\\', '/')
ASSETS = r'D:\Desktop\风哥PDF签章App\tests\assets'
PDF = os.path.join(ASSETS, '_fixed.pdf')
SEAL = os.path.join(ASSETS, '_fixed_seal.png')
SIGN = os.path.join(ASSETS, '_fixed_sign.png')
OUT = os.path.join(APP, '_build', '_out')
PORT = 9339

PASS = FAIL = 0


def check(name, ok, extra=''):
    global PASS, FAIL
    if ok:
        PASS += 1
        print('  PASS  %s %s' % (name, extra))
    else:
        FAIL += 1
        print('  FAIL  %s %s' % (name, extra))


class CDP:
    def __init__(self, ws):
        self.ws = ws
        self.i = 0

    async def send(self, method, params=None):
        self.i += 1
        mid = self.i
        await self.ws.send(json.dumps({'id': mid, 'method': method, 'params': params or {}}))
        while True:
            msg = json.loads(await asyncio.wait_for(self.ws.recv(), 90))
            if msg.get('id') == mid:
                if 'error' in msg:
                    raise RuntimeError('%s -> %s' % (method, msg['error']))
                return msg.get('result', {})

    async def js(self, expr, awaitp=False):
        r = await self.send('Runtime.evaluate', {
            'expression': expr, 'returnByValue': True,
            'awaitPromise': awaitp, 'userGesture': True})
        res = r.get('result', {})
        if r.get('exceptionDetails'):
            raise RuntimeError('JS: %s' % json.dumps(r['exceptionDetails'], ensure_ascii=False)[:300])
        return res.get('value')

    async def set_file(self, selector, path):
        doc = await self.send('DOM.getDocument', {'depth': -1})
        node = await self.send('DOM.querySelector', {'nodeId': doc['root']['nodeId'], 'selector': selector})
        nid = node.get('nodeId')
        if not nid:
            raise RuntimeError('selector not found: %s' % selector)
        await self.send('DOM.setFileInputFiles', {'files': [path], 'nodeId': nid})

    async def wait_for(self, expr, label, timeout=25):
        t0 = time.time()
        while time.time() - t0 < timeout:
            try:
                if await self.js(expr):
                    return True
            except Exception:
                pass
            await asyncio.sleep(0.25)
        raise RuntimeError('timeout waiting: %s' % label)

    async def click_xy(self, x, y):
        for t in ('mousePressed', 'mouseReleased'):
            await self.send('Input.dispatchMouseEvent', {
                'type': t, 'x': x, 'y': y, 'button': 'left', 'clickCount': 1})

    async def drag(self, x1, y1, x2, y2, steps=8):
        await self.send('Input.dispatchMouseEvent', {
            'type': 'mousePressed', 'x': x1, 'y': y1, 'button': 'left', 'clickCount': 1})
        for k in range(1, steps + 1):
            await self.send('Input.dispatchMouseEvent', {
                'type': 'mouseMoved', 'button': 'left', 'buttons': 1,
                'x': x1 + (x2 - x1) * k / steps, 'y': y1 + (y2 - y1) * k / steps})
            await asyncio.sleep(0.02)
        await self.send('Input.dispatchMouseEvent', {
            'type': 'mouseReleased', 'x': x2, 'y': y2, 'button': 'left', 'clickCount': 1})

    async def shot(self, name):
        r = await self.send('Page.captureScreenshot', {'format': 'png', 'captureBeyondViewport': False})
        path = os.path.join(OUT, name)
        with open(path, 'wb') as f:
            f.write(base64.b64decode(r['data']))
        return path


async def main():
    if not os.path.isfile(PDF):
        print('缺少测试素材:', PDF)
        return 1
    os.makedirs(OUT, exist_ok=True)
    profile = tempfile.mkdtemp(prefix='tbx_')
    proc = subprocess.Popen([
        CHROME, '--headless=new', '--remote-debugging-port=%d' % PORT,
        '--user-data-dir=' + profile, '--window-size=1440,1000',
        '--no-first-run', '--no-default-browser-check', '--disable-gpu',
        'about:blank'], stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL)
    ws = None
    try:
        ver = None
        for _ in range(60):
            try:
                import urllib.request
                ver = json.loads(urllib.request.urlopen('http://127.0.0.1:%d/json/version' % PORT, timeout=2).read())
                break
            except Exception:
                await asyncio.sleep(0.3)
        if not ver:
            print('Chrome 没起来')
            return 1
        tabs = json.loads(urllib.request.urlopen('http://127.0.0.1:%d/json' % PORT, timeout=5).read())
        target = [t for t in tabs if t['type'] == 'page'][0]
        ws = await websockets.connect(target['webSocketDebuggerUrl'], max_size=80 * 1024 * 1024)
        cdp = CDP(ws)
        await cdp.send('Page.enable')
        await cdp.send('Runtime.enable')
        await cdp.send('DOM.enable')

        print('1) 打开页面，真实输入密码')
        await cdp.send('Page.navigate', {'url': INDEX})
        await asyncio.sleep(2.0)
        check('密码门先挡住', await cdp.js("getComputedStyle(document.getElementById('pw-overlay')).display !== 'none'"))
        await cdp.send('Input.insertText', {'text': '692026'})
        for t in ('keyDown', 'keyUp'):
            await cdp.send('Input.dispatchKeyEvent', {
                'type': t, 'key': 'Enter', 'code': 'Enter',
                'windowsVirtualKeyCode': 13, 'nativeVirtualKeyCode': 13})
        await asyncio.sleep(0.6)
        check('密码正确后可进入', await cdp.js("getComputedStyle(document.getElementById('pw-overlay')).display === 'none'"))

        await cdp.send('Page.reload')
        await asyncio.sleep(2.5)
        check('刷新后不再卡在密码页', await cdp.js("getComputedStyle(document.getElementById('pw-overlay')).display === 'none'"))

        cards = await cdp.js("document.querySelectorAll('.tool-card').length")
        check('首页 7 张卡片', cards == 7, '(实际 %s)' % cards)
        names = await cdp.js("[...document.querySelectorAll('.tool-card .name')].map(e=>e.textContent)")
        check('出现「盖章签字」', '盖章签字' in (names or []), str(names))
        check('版本号 v1.1.0', (await cdp.js("document.getElementById('versionLabel').textContent")) == 'v1.1.0')
        await cdp.shot('tbx_1_home.png')

        print('2) 打开签字工具')
        await cdp.js("[...document.querySelectorAll('.tool-card')].find(c=>c.textContent.includes('盖章签字')).click()")
        await cdp.wait_for("!!document.querySelector('.st-app')", 'st-app')
        check('工具界面挂载', True)
        check('空状态提示可见', await cdp.js("getComputedStyle(document.getElementById('stEmpty')).display !== 'none'"))
        await cdp.shot('tbx_2_tool.png')

        print('3) 上传 PDF')
        await cdp.set_file('#stFilePdf', PDF)
        await cdp.wait_for("document.querySelectorAll('.st-thumb').length > 1", 'thumbs', 40)
        pages = await cdp.js("document.querySelectorAll('.st-thumb').length")
        check('缩略图生成', pages >= 10, '(%s 页)' % pages)
        check('显示文件名', (await cdp.js("document.getElementById('stPdfName').textContent")).endswith('.pdf'))
        check('画布已渲染', await cdp.js("document.getElementById('stCanvas').width > 200"))
        await cdp.wait_for("document.getElementById('stCanvas').width > 200", 'canvas')
        await cdp.shot('tbx_3_pdf.png')

        print('4) 上传公章 + 签字')
        await cdp.set_file('#stFileSeal', SEAL)
        await cdp.wait_for("!!document.querySelector('#stSealThumb img')", 'seal thumb')
        check('公章载入', True)
        await cdp.set_file('#stFileSign', SIGN)
        await cdp.wait_for("!!document.querySelector('#stSignThumb img')", 'sign thumb')
        check('签字载入', True)
        size = await cdp.js("(function(){var i=document.querySelector('#stSealThumb img');return [i.naturalWidth,i.naturalHeight]})()")
        check('公章图片可解码', bool(size and size[0] > 0), str(size))

        print('5) 点页面放公章')
        rect = await cdp.js("""(function(){
          var ov=document.getElementById('stOverlay'), r=ov.getBoundingClientRect();
          var st=document.getElementById('stStage'), sr=st.getBoundingClientRect();
          var x=r.x+r.width*0.5;
          var y=Math.min(r.y+r.height*0.75, sr.y+sr.height-40);
          var hit=document.elementFromPoint(x,y);
          return {x:x,y:y,w:r.width,h:r.height,hit:hit?hit.className:null,
                  stageH:sr.height, innerH:window.innerHeight};
        })()""")
        print('    点 %.0f,%.0f 命中 %r (stage %.0f / win %.0f)' % (
            rect['x'], rect['y'], rect['hit'], rect['stageH'], rect['innerH']))
        await cdp.js("document.getElementById('stArmSeal').click()")
        check('放置模式高亮', await cdp.js("document.getElementById('stArmSeal').classList.contains('armed')"))
        await cdp.click_xy(rect['x'], rect['y'])
        await cdp.wait_for("document.querySelectorAll('.st-obj').length === 1", 'seal obj')
        check('公章放上去了', True)
        check('对象被选中', await cdp.js("!!document.querySelector('.st-obj.sel')"))
        await cdp.shot('tbx_4_seal.png')

        print('6) 拖动')
        before = await cdp.js("(function(){var e=document.querySelector('.st-obj');return e.style.left})()")
        b = await cdp.js("(function(){var r=document.querySelector('.st-obj').getBoundingClientRect();return [r.x+r.width/2, r.y+r.height/2]})()")
        await cdp.drag(b[0], b[1], b[0] - 70, b[1] - 120)
        after = await cdp.js("(function(){var e=document.querySelector('.st-obj');return e.style.left})()")
        check('拖动改变了位置', before != after, '%s -> %s' % (before, after))

        print('7) 调尺寸/角度')
        await cdp.js("(function(){var r=document.getElementById('stDia');r.value=55;r.dispatchEvent(new Event('input'))})()")
        w55 = await cdp.js("document.querySelector('.st-obj').style.width")
        await cdp.js("(function(){var r=document.getElementById('stDia');r.value=40;r.dispatchEvent(new Event('input'))})()")
        w40 = await cdp.js("document.querySelector('.st-obj').style.width")
        check('直径滑块改变章大小', w55 != w40, '%s vs %s' % (w55, w40))
        check('复位按钮恢复可用状态', await cdp.js("document.getElementById('stDiaReset').disabled === true"))
        await cdp.js("(function(){var r=document.getElementById('stAng');r.value=25;r.dispatchEvent(new Event('input'))})()")
        check('角度作用于公章', '25deg' in (await cdp.js("document.querySelector('.st-obj').style.transform")))
        await cdp.js("document.getElementById('stAngReset').click()")
        check('角度复位到 0', '0deg' in (await cdp.js("document.querySelector('.st-obj').style.transform")))

        print('8) Delete 删除（滑块动过之后）')
        await cdp.js("document.getElementById('stDia').focus()")
        await cdp.send('Input.dispatchKeyEvent', {'type': 'keyDown', 'key': 'Delete', 'code': 'Delete', 'windowsVirtualKeyCode': 46, 'nativeVirtualKeyCode': 46})
        await cdp.send('Input.dispatchKeyEvent', {'type': 'keyUp', 'key': 'Delete', 'code': 'Delete', 'windowsVirtualKeyCode': 46, 'nativeVirtualKeyCode': 46})
        await asyncio.sleep(0.6)
        check('滑块有焦点时 Delete 仍然生效', (await cdp.js("document.querySelectorAll('.st-obj').length")) == 0)

        print('9) 自动定位')
        await cdp.js("document.getElementById('stAuto').click()")
        await asyncio.sleep(3.5)
        n_obj = await cdp.js("document.querySelectorAll('.st-obj').length")
        check('自动定位放上了东西', n_obj >= 1, '(%s 个)' % n_obj)
        log_txt = await cdp.js("document.getElementById('stLog').innerText")
        print('    日志:', log_txt.replace('\n', ' | ')[:220])
        await cdp.shot('tbx_5_auto.png')

        print('10) 生成 PDF')
        await cdp.js("document.getElementById('stGo').click()")
        ok = await cdp.wait_for("!!window._resultData", 'result', 40)
        info = await cdp.js("(function(){var r=window._resultData;return {name:r.name, size:r.data.size}})()")
        check('生成了 PDF', bool(info) and info['size'] > 1000, str(info))
        check('文件名带 _盖章', '_盖章.pdf' in (info or {}).get('name', ''))
        await cdp.shot('tbx_6_done.png')

        # 把结果拉回来用 PyMuPDF 验真
        b64 = await cdp.js(
            "(async()=>{const b=await window._resultData.data.arrayBuffer();"
            "let s='';const u=new Uint8Array(b);"
            "for(let i=0;i<u.length;i+=8192){s+=String.fromCharCode.apply(null,u.subarray(i,i+8192));}"
            "return btoa(s)})()", awaitp=True)
        if b64:
            raw = base64.b64decode(b64)
            out_pdf = os.path.join(OUT, 'tbx_result.pdf')
            with open(out_pdf, 'wb') as f:
                f.write(raw)
            import fitz
            d = fitz.open(out_pdf)
            imgs = sum(len(p.get_images(full=True)) for p in d)
            check('PDF 里真的有图', imgs >= 1, '(%d 张图 / %d 页)' % (imgs, d.page_count))
            d.close()
            print('    结果文件:', out_pdf, '%.0f KB' % (len(raw) / 1024.0))

        print('11) 切回首页再进来（状态要重建）')
        await cdp.js("showHome()")
        await asyncio.sleep(0.4)
        await cdp.js("[...document.querySelectorAll('.tool-card')].find(c=>c.textContent.includes('盖章签字')).click()")
        await cdp.wait_for("!!document.querySelector('.st-app')", 'remount')
        check('重新打开是干净的', (await cdp.js("document.querySelectorAll('.st-obj').length")) == 0)
        check('其他工具仍正常', await cdp.js("(function(){showTool('merge-pdfs');return !!document.getElementById('runBtn')})()"))

        print()
        print('%d passed, %d failed' % (PASS, FAIL))
        return 0 if FAIL == 0 else 1
    finally:
        if ws:
            await ws.close()
        proc.terminate()
        try:
            proc.wait(timeout=6)
        except Exception:
            proc.kill()
        shutil.rmtree(profile, ignore_errors=True)


if __name__ == '__main__':
    raise SystemExit(asyncio.run(main()))

# -*- coding: utf-8 -*-
"""验证"旋转角度"导出到 PDF 后的方向：屏幕上顺时针 45° = PDF 里视觉顺时针 45°。"""
import asyncio, base64, json, os, shutil, subprocess, tempfile, urllib.request
import websockets

CHROME = r'C:\Program Files\Google\Chrome\Application\chrome.exe'
INDEX = 'file:///D:/Desktop/系统文件夹/Documents/网页应用/index.html'
ASSETS = r'D:\Desktop\风哥PDF签章App\tests\assets'
OUT = r'D:\Desktop\系统文件夹\Documents\网页应用\_build\_out'
PORT = 9347


async def main():
    profile = tempfile.mkdtemp(prefix='ang_')
    p = subprocess.Popen([CHROME, '--headless=new', '--remote-debugging-port=%d' % PORT,
                          '--user-data-dir=' + profile, '--window-size=1440,1000',
                          '--no-first-run', '--disable-gpu', 'about:blank'],
                         stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL)
    try:
        for _ in range(60):
            try:
                tabs = json.loads(urllib.request.urlopen('http://127.0.0.1:%d/json' % PORT, timeout=2).read())
                break
            except Exception:
                await asyncio.sleep(0.3)
        t = [x for x in tabs if x['type'] == 'page'][0]
        ws = await websockets.connect(t['webSocketDebuggerUrl'], max_size=96 * 1024 * 1024)
        i = [0]

        async def send(m, pr=None):
            i[0] += 1
            mid = i[0]
            await ws.send(json.dumps({'id': mid, 'method': m, 'params': pr or {}}))
            while True:
                msg = json.loads(await ws.recv())
                if msg.get('id') == mid:
                    return msg.get('result', {})

        async def js(e, awaitp=False):
            r = await send('Runtime.evaluate', {'expression': e, 'returnByValue': True,
                                                'awaitPromise': awaitp, 'userGesture': True})
            if r.get('exceptionDetails'):
                raise RuntimeError(json.dumps(r['exceptionDetails'], ensure_ascii=False)[:300])
            return r.get('result', {}).get('value')

        await send('Page.enable'); await send('Runtime.enable'); await send('DOM.enable')
        await send('Page.navigate', {'url': INDEX}); await asyncio.sleep(2)
        await js("sessionStorage.setItem('fengge-toolbox-auth','ok')")
        await send('Page.reload'); await asyncio.sleep(2.5)
        await js("[...document.querySelectorAll('.tool-card')].find(c=>c.textContent.includes('盖章签字')).click()")
        await asyncio.sleep(0.5)

        doc = await send('DOM.getDocument', {'depth': -1})
        for sel, f in (('#stFilePdf', '_fixed.pdf'), ('#stFileSeal', '_fixed_seal.png')):
            n = await send('DOM.querySelector', {'nodeId': doc['root']['nodeId'], 'selector': sel})
            await send('DOM.setFileInputFiles', {'files': [ASSETS + '\\' + f], 'nodeId': n['nodeId']})
            await asyncio.sleep(3)

        # 放一个章到页面中间，再转 45°
        await js("document.getElementById('stArmSeal').click()")
        pos = await js("""(function(){var o=document.getElementById('stOverlay').getBoundingClientRect();
            return [o.x+o.width*0.5, o.y+Math.min(o.height*0.5, 300)]})()""")
        for tt in ('mousePressed', 'mouseReleased'):
            await send('Input.dispatchMouseEvent', {'type': tt, 'x': pos[0], 'y': pos[1],
                                                    'button': 'left', 'clickCount': 1})
        await asyncio.sleep(0.6)
        await js("(function(){var r=document.getElementById('stAng');r.value=45;r.dispatchEvent(new Event('input'))})()")
        await asyncio.sleep(0.4)
        tr = await js("document.querySelector('.st-obj').style.transform")
        print('屏幕上:', tr)

        await js("document.getElementById('stGo').click()")
        for _ in range(80):
            if await js("!!window._resultData"):
                break
            await asyncio.sleep(0.4)
        b64 = await js("(async()=>{const b=await window._resultData.data.arrayBuffer();"
                       "let s='';const u=new Uint8Array(b);"
                       "for(let i=0;i<u.length;i+=8192){s+=String.fromCharCode.apply(null,u.subarray(i,i+8192));}"
                       "return btoa(s)})()", awaitp=True)
        raw = base64.b64decode(b64)
        path = os.path.join(OUT, 'angle45.pdf')
        with open(path, 'wb') as f:
            f.write(raw)
        print('已保存', path, '%.0f KB' % (len(raw) / 1024))

        import fitz
        d = fitz.open(path)
        pix = d[-1].get_pixmap(matrix=fitz.Matrix(2, 2))
        pix.save(os.path.join(OUT, 'angle45_page.png'))
        print('渲染完成', pix.width, 'x', pix.height)
        d.close()
        await ws.close()
    finally:
        p.terminate(); shutil.rmtree(profile, ignore_errors=True)


asyncio.run(main())

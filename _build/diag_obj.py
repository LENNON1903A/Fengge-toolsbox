# -*- coding: utf-8 -*-
import asyncio, json, shutil, subprocess, tempfile, urllib.request
import websockets
CHROME = r'C:/Program Files\Google\Chrome\Application\chrome.exe'
INDEX = 'file:///D:/Desktop/系统文件夹/Documents/网页应用/index.html'
ASSETS = r'D:/Desktop/风哥PDF签章App/tests/assets'
PORT = 9343

async def main():
    profile = tempfile.mkdtemp(prefix='diag2_')
    p = subprocess.Popen([CHROME,'--headless=new','--remote-debugging-port=%d'%PORT,
        '--user-data-dir='+profile,'--window-size=1440,1000','--no-first-run','--disable-gpu','about:blank'],
        stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL)
    try:
        for _ in range(60):
            try:
                tabs = json.loads(urllib.request.urlopen('http://127.0.0.1:%d/json'%PORT, timeout=2).read()); break
            except Exception: await asyncio.sleep(0.3)
        t=[x for x in tabs if x['type']=='page'][0]
        ws = await websockets.connect(t['webSocketDebuggerUrl'], max_size=64*1024*1024)
        i=[0]
        async def send(m,pr=None):
            i[0]+=1; mid=i[0]
            await ws.send(json.dumps({'id':mid,'method':m,'params':pr or {}}))
            while True:
                msg=json.loads(await ws.recv())
                if msg.get('id')==mid: return msg.get('result',{})
        async def js(e,aw=False):
            r=await send('Runtime.evaluate',{'expression':e,'returnByValue':True,'awaitPromise':aw,'userGesture':True})
            if r.get('exceptionDetails'): return 'ERR '+json.dumps(r['exceptionDetails'],ensure_ascii=False)[:400]
            return r.get('result',{}).get('value')
        await send('Page.enable'); await send('Runtime.enable'); await send('DOM.enable')
        await send('Page.navigate',{'url':INDEX}); await asyncio.sleep(2)
        await js("sessionStorage.setItem('fengge-toolbox-auth','ok')")
        await send('Page.reload'); await asyncio.sleep(2.5)
        await js("[...document.querySelectorAll('.tool-card')].find(c=>c.textContent.includes('盖章签字')).click()")
        await asyncio.sleep(0.5)
        doc = await send('DOM.getDocument',{'depth':-1})
        for sel, f in (('#stFilePdf','_fixed.pdf'),('#stFileSeal','_fixed_seal.png'),('#stFileSign','_fixed_sign.png')):
            n = await send('DOM.querySelector',{'nodeId':doc['root']['nodeId'],'selector':sel})
            await send('DOM.setFileInputFiles',{'files':[ASSETS+'\\'+f],'nodeId':n['nodeId']})
            await asyncio.sleep(3.5)
        await js("document.getElementById('stAuto').click()")
        await asyncio.sleep(4)
        print('--- 对象 ---')
        print(json.dumps(await js("""[...document.querySelectorAll('.st-obj')].map(e=>{
            const im=e.querySelector('img');
            const r=e.getBoundingClientRect();
            return {cls:e.className, left:e.style.left, top:e.style.top, w:e.style.width, h:e.style.height,
                    rot:e.style.transform, natural:[im.naturalWidth,im.naturalHeight],
                    src:(im.src||'').slice(0,30), rect:[Math.round(r.x),Math.round(r.y),Math.round(r.width),Math.round(r.height)]};
        })"""), ensure_ascii=False, indent=1))
        print('--- 舞台 ---')
        print(json.dumps(await js("""(function(){
            const s=document.getElementById('stStage'), o=document.getElementById('stOverlay');
            return {scrollTop:s.scrollTop, clientH:s.clientHeight, scrollH:s.scrollHeight,
                    ovH:o.style.height, ovRect:Math.round(o.getBoundingClientRect().height),
                    pageH:document.getElementById('stCanvas').height};
        })()"""), ensure_ascii=False))
        print('--- 日志 ---')
        print(await js("document.getElementById('stLog').innerText"))
        await ws.close()
    finally:
        p.terminate(); shutil.rmtree(profile, ignore_errors=True)

asyncio.run(main())

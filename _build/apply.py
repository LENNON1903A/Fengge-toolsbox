# -*- coding: utf-8 -*-
"""
把 _build/sign.tool.js + _build/sign.style.css 注入单文件站点 index.html。

用法：
    python _build/apply.py            # 注入（可重复执行，幂等）
    python _build/apply.py --check    # 只检查当前是否已注入

设计目的：index.html 有 700KB（pdf-lib 以 base64 内嵌），直接手改容易出错，
所以签章工具的源码单独放 _build/，每次改完重跑本脚本即可。

幂等做法：先删掉上一次插进去的标记块，再用"柔性正则"吃掉残留空行后重新插入，
因此连续跑十次和跑一次的结果完全相同。
"""
import io
import os
import re
import sys

HERE = os.path.dirname(os.path.abspath(__file__))
APP = os.path.dirname(HERE)
TARGET = os.path.join(APP, 'index.html')
JS_SRC = os.path.join(HERE, 'sign.tool.js')
CSS_SRC = os.path.join(HERE, 'sign.style.css')
VERSION_TXT = os.path.join(APP, 'version.txt')

NEW_VERSION = 'v1.1.0'

M_CSS = ('/*==SIGN-TOOL:CSS:BEGIN==*/', '/*==SIGN-TOOL:CSS:END==*/')
M_JS = ('/*==SIGN-TOOL:JS:BEGIN==*/', '/*==SIGN-TOOL:JS:END==*/')
M_ENTRY = ('/*==SIGN-TOOL:ENTRY:BEGIN==*/', '/*==SIGN-TOOL:ENTRY:END==*/')
M_HOOK = ('/*==SIGN-TOOL:HOOK:BEGIN==*/', '/*==SIGN-TOOL:HOOK:END==*/')

ENTRY_BODY = (
    M_ENTRY[0] +
    "\n  ,{ id:'sign-stamp', name:'盖章签字', icon:'\U0001F50F',"
    " desc:'把公章和手写签字放到 PDF 上，位置随你拖，文件不出本机',"
    " color:'#fdecea', custom:function(c){ SignStampTool.mount(c); } }" +
    M_ENTRY[1]
)

HOOK_BODY = (
    M_HOOK[0] + '\n'
    '  if (tool.custom) { tool.custom(container); return; }   // 自定义界面（盖章签字）\n'
    '  ' + M_HOOK[1]
)


def strip_all(text):
    for pair in (M_CSS, M_JS, M_ENTRY, M_HOOK):
        text = re.sub(re.escape(pair[0]) + r'[\s\S]*?' + re.escape(pair[1]), '', text)
    return text


def swap(text, pattern, build, what):
    """用柔性正则把锚点归一化后重新插入，保证重复执行不会累积空行。"""
    m = list(re.finditer(pattern, text))
    if len(m) != 1:
        raise SystemExit('[FAIL] %s 命中 %d 次（要求 1 次）' % (what, len(m)))
    found = m[0]
    return text[:found.start()] + build(found.group(0)) + text[found.end():]


def main():
    check_only = '--check' in sys.argv
    css = io.open(CSS_SRC, encoding='utf-8').read().strip()
    js = io.open(JS_SRC, encoding='utf-8').read().strip()

    raw = io.open(TARGET, encoding='utf-8', newline='').read()
    text = raw.replace('\r\n', '\n')

    if check_only:
        has = all(m[0] in text for m in (M_CSS, M_JS, M_ENTRY, M_HOOK))
        print('已注入' if has else '未注入')
        return 0 if has else 1

    text = strip_all(text)

    # 1. CSS：插在 </style> 前
    text = swap(text, r'\n\s*</style>',
                lambda _: '\n' + M_CSS[0] + '\n' + css + '\n' + M_CSS[1] + '\n</style>',
                'CSS 锚点')

    # 2. TOOLS 数组末尾追加卡片
    text = swap(text, r'\n\s*\];\s*\n\s*function parsePages',
                lambda _: '\n' + ENTRY_BODY + '\n];\n\nfunction parsePages',
                'TOOLS 锚点')

    # 3. showTool 支持 tool.custom 自定义界面
    text = swap(text, r"\n\s*const form = document\.createElement\('div'\);",
                lambda s: '\n' + HOOK_BODY + '\n  const form = document.createElement(\'div\');',
                'showTool 锚点')

    # 4. 工具主体：放在 init() 之前
    text = swap(text, r'\n\s*init\(\);\s*\n</script>',
                lambda _: '\n' + M_JS[0] + '\n' + js + '\n' + M_JS[1] + '\n\ninit();\n</script>',
                'init 锚点')

    # 5. 顺手修掉站点原有缺陷：刷新后密码门不会消失
    #    （sessionStorage 有标记就直接 return，遮罩从来没被隐藏，用户会卡在蓝屏）
    gate_old = "  if (pw === 'ok') { return; }"
    gate_new = ("  if (pw === 'ok') { "
                "document.getElementById('pw-overlay').style.display = 'none'; return; }")
    if gate_old in text:
        text = text.replace(gate_old, gate_new, 1)
        print('密码门刷新 bug : 已修')
    elif gate_new in text:
        print('密码门刷新 bug : 之前已修')
    else:
        raise SystemExit('[FAIL] 找不到密码门的那段判断，请检查 index.html')

    # 6. 版本号（两处都走正则，保证可重复执行）
    text, n1 = re.subn(r"const VERSION = 'v[\d.]+';", "const VERSION = '%s';" % NEW_VERSION, text)
    text, n2 = re.subn(r'(<span class="version" id="versionLabel">)v[\d.]+(</span>)',
                       r'\g<1>%s\g<2>' % NEW_VERSION, text)
    if n1 != 1 or n2 != 1:
        raise SystemExit('[FAIL] 版本号替换异常 VERSION=%d label=%d' % (n1, n2))

    io.open(TARGET, 'w', encoding='utf-8', newline='').write(text.replace('\n', '\r\n'))
    io.open(VERSION_TXT, 'w', encoding='utf-8', newline='').write(NEW_VERSION + '\n')

    out = io.open(TARGET, encoding='utf-8').read()
    print('index.html : %.0f KB' % (os.path.getsize(TARGET) / 1024.0))
    print('CSS        :', 'OK' if M_CSS[0] in out else 'MISSING')
    print('TOOLS 条目 :', 'OK' if M_ENTRY[0] in out else 'MISSING')
    print('showTool   :', 'OK' if M_HOOK[0] in out else 'MISSING')
    print('JS 主体    :', 'OK' if M_JS[0] in out else 'MISSING')
    print('卡片数量   :', out.count("custom:function(c)") + 6)
    print('版本       :', NEW_VERSION)
    return 0


if __name__ == '__main__':
    sys.exit(main())

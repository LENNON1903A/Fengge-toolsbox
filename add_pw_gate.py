import re

path = r"D:\Desktop\系统文件夹\Documents\网页应用\index.html"
with open(path, "r", encoding="utf-8") as f:
    html = f.read()

# 密码 overlay CSS（加到 style 末尾）
overlay_css = """
/* 密码保护 */
#pw-overlay{position:fixed;top:0;left:0;width:100vw;height:100vh;background:#1a237e;z-index:9999;display:flex;align-items:center;justify-content:center;flex-direction:column;color:#fff}
#pw-overlay .pw-box{text-align:center;max-width:320px;padding:20px}
#pw-overlay .pw-box h2{font-size:22px;margin-bottom:4px}
#pw-overlay .pw-box .sub{font-size:13px;opacity:.6;margin-bottom:20px}
#pw-overlay .pw-box input{border:none;padding:12px 16px;font-size:16px;border-radius:8px;width:100%;text-align:center;outline:none}
#pw-overlay .pw-box .err{color:#ff8a80;font-size:13px;margin-top:8px;display:none}
"""
# 找到 </style> 标签前插入
pos = html.find("</style>")
if pos > 0:
    html = html[:pos] + overlay_css + "\n" + html[pos:]
    print("CSS: OK")
else:
    print("CSS: FAIL")


# 密码 overlay HTML（加到 <body> 后面）
overlay_html = """
<div id="pw-overlay">
  <div class="pw-box">
    <h2>风哥万能工具箱</h2>
    <div class="sub">输入密码后使用</div>
    <input type="password" id="pw-input" placeholder="请输入密码" maxlength="20">
    <div class="err" id="pw-err">密码错误，请重试</div>
  </div>
</div>
"""
pos = html.find("<body>")
if pos > 0:
    html = html[:pos+6] + overlay_html + html[pos+6:]
    print("HTML: OK")
else:
    print("HTML: FAIL")


# 密码验证逻辑（加到 <script> 开头）
pw_script = """
// 密码验证
(function(){
  var pw = sessionStorage.getItem('fengge-toolbox-auth');
  if (pw === 'ok') { return; }
  var input = document.getElementById('pw-input');
  var err = document.getElementById('pw-err');
  input.focus();
  input.addEventListener('keydown', function(e) {
    if (e.key === 'Enter') {
      if (btoa(input.value) === 'NjkyMDI2') {
        sessionStorage.setItem('fengge-toolbox-auth', 'ok');
        document.getElementById('pw-overlay').style.display = 'none';
      } else {
        err.style.display = 'block';
        input.value = '';
        input.focus();
      }
    }
  });
})();
"""
pos = html.find("<script>")
# 找到最后一个 <script> 标签（内联脚本的开始）
# 跳过 CDN 脚本
scripts = list(re.finditer(r"<script>", html))
if scripts:
    last_script = scripts[-1]
    pos = last_script.start() + len("<script>")
    html = html[:pos] + pw_script + html[pos:]
    print("Script: OK")
else:
    print("Script: FAIL")


with open(path, "w", encoding="utf-8") as f:
    f.write(html)

# 验证
c = open(path, "r", encoding="utf-8").read()
print(f"密码门: {'OK' if 'pw-overlay' in c else 'FAIL'}")
print(f"密码验证: {'OK' if 'NjkyMDI2' in c else 'FAIL'}")
print(f"文件大小: {len(c.encode('utf-8'))/1024:.0f} KB")

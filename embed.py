import urllib.request, base64

path = r"D:\Desktop\系统文件夹\Documents\网页应用\index.html"
with open(path, "r", encoding="utf-8") as f:
    html = f.read()

# 下载 pdf-lib
url = "https://cdn.jsdelivr.net/npm/pdf-lib@1.17.1/dist/pdf-lib.min.js"
lib = urllib.request.urlopen(url).read()
lib_b64 = base64.b64encode(lib).decode("ascii")

# 找一个换行符，适配 CRLF 和 LF
old = '<script src="https://cdn.jsdelivr.net/npm/pdf-lib@1.17.1/dist/pdf-lib.min.js">\n</script>'
if old not in html:
    old = '<script src="https://cdn.jsdelivr.net/npm/pdf-lib@1.17.1/dist/pdf-lib.min.js">\r\n</script>'

new = f'<script src="data:text/javascript;base64,{lib_b64}"></script>'
new_html = html.replace(old, new)

with open(path, "w", encoding="utf-8") as f:
    f.write(new_html)

size = len(new_html.encode("utf-8"))

# 验证
c = open(path, "r", encoding="utf-8").read()
print(f"文件大小: {size/1024:.0f} KB")
print(f"data URI: {'OK' if 'data:text/javascript;base64' in c else 'MISSING'}")
print(f"CDN残留: {'CLEAN' if 'cdn.jsdelivr.net/npm/pdf-lib' not in c else 'STILL EXISTS'}")
print(f"自检: {'OK' if 'typeof PDFLib' in c else 'MISSING'}")
print(f"解构: {'OK' if 'const { PDFDocument } = PDFLib;' in c else 'MISSING'}")

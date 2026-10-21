#!/usr/bin/env python3
"""把 extract.py 产出的 JSON 渲染成交付物：

    <name>.md               Markdown（图片用远程 URL）
    <name>_微信笔记.html     可编辑 + 一键复制（图 + 文）
    <name>_纯文本.txt        纯文字备份

用法：
    python build.py extract.json [--outdir .] [--name 作业]
"""
import argparse
import base64
import io
import json
import os
import re
import urllib.request

UA = {"User-Agent": "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 Chrome/124 Safari/537.36"}

# 口令高亮：微信笔记里那支「高亮」笔的默认底色就是 #FFEDB2（截图取色 rgb(255,237,178)）。
# 复制成富文本时带的是行内 background-color，粘进微信笔记会落成「高亮」，不是普通底色。
HL = {"color": "255,237,178"}   # 设成 None 即关闭高亮

# 一页里可能出现的几种口令形态；顺序 = 优先匹配顺序（长的在前）
CODE_ANY_RE = re.compile(
    "|".join([
        r"\$[A-Za-z0-9]{6,24}\$",                     # 老口令 $xxxx$
        r"\d{0,2}฿[A-Za-z0-9]{4,24}\)\s*/\s*CA\d{2}",  # 淘礼金 / 红包 9฿xxx)/ CA38
        r"\d[￥¥][^￥¥\n]{2,40}[￥¥]://",                # 新版 8￥ CZ0001 xxx￥://
        r"[$￥¥][^$￥¥\s]{4,40}[$￥¥]",                  # ￥xxxxxxxxxxx￥ / $...$
    ])
)


def highlight_codes(line):
    """把一行里的口令包成带背景色的 <span>，其余字符原样。"""
    color = HL.get("color")
    if not color or not line:
        return line
    out, last = [], 0
    for m in CODE_ANY_RE.finditer(line):
        s, e = m.span()
        if s < last:
            continue
        out.append(line[last:s])
        out.append('<span style="background-color:rgb(%s)">%s</span>' % (color, m.group(0)))
        last = e
    out.append(line[last:])
    return "".join(out)


def normalize_codes(codes):
    """长 URL 先替换，避免短 URL 是长 URL 前缀时误伤。"""
    pairs = []
    for url, val in codes.items():
        reps = [val] if isinstance(val, str) else [x for x in val if x]
        if url and reps:
            pairs.append((url, reps))
    return sorted(pairs, key=lambda kv: -len(kv[0]))


def substitute(line, pairs):
    """把一行里的 URL 换成口令，返回「一行或多行」。

    一个链接可能对应多个口令（淘礼金页常有「礼金 + 优惠券」两步）：
    该行只有链接时拆成多行，还有别的文字时用空格内联。
    """
    for url, reps in pairs:
        if url and url in line:
            if line.strip() == url:
                return list(reps)
            line = line.replace(url, " ".join(reps))
    return [line]


def build_blocks(data):
    pairs = normalize_codes(data.get("codes", {}))
    out = []
    for b in data["blocks"]:
        if b["type"] == "img":
            out.append({"type": "img", "src": b["src"], "alt": b.get("alt", ""),
                        "soft": bool(b.get("soft"))})
        else:
            lines = []
            for l in b["lines"]:
                lines.extend(substitute(l, pairs))
            out.append({"type": "text", "lines": lines, "soft": bool(b.get("soft"))})
    if out:
        out[0]["soft"] = False
    return out


def join_blocks(strs, blocks):
    """块之间默认插分隔线；soft 块（展开进来的子页内容）紧跟上一块。"""
    out = []
    for i, s in enumerate(strs):
        if i:
            out.append("\n" if blocks[i].get("soft") else SEP_MD)
        out.append(s)
    return "".join(out)


SEP_MD = "\n\n---\n\n"


def to_md(blocks):
    parts = []
    for b in blocks:
        if b["type"] == "img":
            parts.append("![](%s)" % b["src"])
        else:
            parts.append("\n".join(b["lines"]))
    return join_blocks(parts, blocks) + "\n"


def to_plain(blocks):
    keep = [b for b in blocks if b["type"] == "text"]
    return join_blocks(["\n".join(b["lines"]) for b in keep], keep) + "\n"


def fetch_and_encode(url, max_w=720, quality=82):
    """下载 + 压缩 + base64，用于「逐张复制」走图片剪贴板；失败返回 None。"""
    try:
        req = urllib.request.Request(url, headers=UA)
        with urllib.request.urlopen(req, timeout=30) as r:
            raw = r.read()
    except Exception:
        return None
    try:
        from PIL import Image
        im = Image.open(io.BytesIO(raw))
        if im.mode not in ("RGB", "L"):
            im = im.convert("RGB")
        if im.width > max_w:
            im = im.resize((max_w, round(im.height * max_w / im.width)), Image.LANCZOS)
        buf = io.BytesIO()
        im.save(buf, "JPEG", quality=quality, optimize=True)
        raw = buf.getvalue()
        mime = "image/jpeg"
    except Exception:
        mime = "image/png" if raw[:8].startswith(b"\x89PNG") else "image/jpeg"
    b64 = base64.b64encode(raw).decode()
    return "data:%s;base64,%s" % (mime, b64)


def to_html_sheet(blocks, hr=True):
    """一行一个 <p>：微信笔记只认段落，<br> 会被吞掉导致整段挤成一行。

    hr=False 时不插块间分隔线（tab 版用）；soft 块本身也不插分隔线。
    """
    segs = []
    for i, b in enumerate(blocks):
        buf = []
        if hr and i and not b.get("soft"):
            buf.append("<hr>")
        if b["type"] == "img":
            buf.append('<p><img src="%s" alt="" width="360"></p>' % b["src"])
        else:
            for ln in b["lines"]:
                buf.append("<p>&nbsp;</p>" if ln == "" else "<p>%s</p>" % highlight_codes(ln))
        segs.append("\n".join(buf))
    return "\n".join(segs)


PAGE = r"""<!DOCTYPE html>
<html lang="zh-CN">
<head>
<meta charset="UTF-8">
<meta name="viewport" content="width=device-width, initial-scale=1.0">
<title>__TITLE__ · 复制到微信笔记</title>
<style>
  * { margin:0; padding:0; box-sizing:border-box; }
  body {
    background:#f3f3f4; color:#1a1a1a; padding:16px 14px 96px;
    font-family:-apple-system,BlinkMacSystemFont,"PingFang SC","Microsoft YaHei",sans-serif;
    display:flex; flex-direction:column; align-items:center; gap:12px;
  }
  .topbar { width:100%; max-width:680px; display:flex; align-items:center; justify-content:space-between; gap:10px; flex-wrap:wrap; }
  .topbar .hint { flex:1 1 100%; order:2; font-size:12px; color:#8a8a8a; line-height:1.55; }
  .topbar .acts { order:1; margin-left:auto; display:flex; gap:8px; }
  .topbar button {
    border:0; background:#07c160; color:#fff; font-weight:600; font-size:13.5px;
    padding:9px 16px; border-radius:20px; cursor:pointer; font-family:inherit;
  }
  .topbar .ghost { background:#fff; color:#4a4a4a; box-shadow:inset 0 0 0 1px #e2e2e2; }
  .topbar button:active { filter:brightness(.94); }
  .imgbar {
    width:100%; max-width:680px; display:flex; align-items:center; gap:8px; flex-wrap:wrap;
    background:#fff; border-radius:10px; padding:10px 12px; box-shadow:0 2px 10px rgba(0,0,0,.04);
  }
  .imgbar .lbl { font-size:12.5px; color:#8a8a8a; }
  .imgbar button {
    border:0; background:#eef7f1; color:#0a8f4d; font-weight:600; font-size:12.5px;
    padding:6px 12px; border-radius:14px; cursor:pointer; font-family:inherit;
  }
  .sheet {
    width:100%; max-width:680px; background:#fff; border-radius:10px;
    padding:22px 20px 30px; box-shadow:0 3px 16px rgba(0,0,0,.05);
    font-size:15px; line-height:1.85; outline:none; word-break:break-word;
  }
  .sheet:focus { box-shadow:0 0 0 2px rgba(7,193,96,.22); }
  .sheet p { margin:0; }
  .sheet hr { border:0; border-top:1px solid #eee; margin:16px 0; }
  .sheet img { display:block; width:330px; max-width:100%; height:auto; margin:6px 0; border-radius:6px; }
  .float {
    position:fixed; left:0; right:0; bottom:0; padding:10px 14px calc(12px + env(safe-area-inset-bottom));
    background:linear-gradient(to top,#f3f3f4 55%,rgba(243,243,244,0)); display:flex; justify-content:center;
  }
  .float button {
    width:100%; max-width:680px; border:0; border-radius:12px; background:#07c160; color:#fff;
    font-size:16px; font-weight:700; padding:14px 0; cursor:pointer; font-family:inherit;
    box-shadow:0 6px 20px rgba(7,193,96,.3);
  }
  .toast {
    position:fixed; left:50%; top:40%; transform:translate(-50%,-50%) scale(.96);
    background:rgba(20,20,20,.86); color:#fff; font-size:14px; line-height:1.6;
    padding:12px 20px; border-radius:10px; opacity:0; pointer-events:none;
    transition:opacity .18s, transform .18s; z-index:99; text-align:center;
    max-width:80vw; white-space:pre-line;
  }
  .toast.show { opacity:1; transform:translate(-50%,-50%) scale(1); }
</style>
</head>
<body>

<div class="topbar">
  <div class="hint">① 点「复制全部」→ 微信笔记粘贴；② 哪张图没进来，光标点到空位，点下面「图N」再粘一次（走图片剪贴板，必定成功）</div>
  <div class="acts">
    <button id="btnPlain" class="ghost">只复制文字</button>
    <button id="btnTop">复制全部</button>
  </div>
</div>

<div class="imgbar">
  <span class="lbl">图片逐个复制：</span>
  __IMGBTNS__
</div>

<div class="sheet" id="editor" contenteditable="true" spellcheck="false">
__BODY__
</div>

<div class="float"><button id="btnBottom">📋 一键复制全部（含图片）</button></div>
<div class="toast" id="toast">已复制</div>

<script>
const editor = document.getElementById('editor');
const toastEl = document.getElementById('toast');
const PLAIN = __PLAIN__;
const IMGDATA = __IMGDATA__;

function toast(msg, ms) {
  toastEl.textContent = msg;
  toastEl.classList.add('show');
  clearTimeout(toastEl._h);
  toastEl._h = setTimeout(() => toastEl.classList.remove('show'), ms || 1900);
}
function copyAll() {
  const btns = [document.getElementById('btnTop'), document.getElementById('btnBottom')];
  const old = btns.map(b => b.textContent);
  btns.forEach(b => b.textContent = '复制中…');
  const sel = window.getSelection();
  const range = document.createRange();
  range.selectNodeContents(editor);
  sel.removeAllRanges();
  sel.addRange(range);
  let ok = false;
  try { ok = document.execCommand('copy'); } catch (e) {}
  sel.removeAllRanges();
  btns.forEach((b, i) => b.textContent = old[i]);
  toast(ok ? '已复制（含图片）\n去微信笔记里长按粘贴' : '复制失败：请手动 Ctrl+A / Ctrl+C');
}
function copyPlain() {
  const ta = document.createElement('textarea');
  ta.value = PLAIN; ta.style.position = 'fixed'; ta.style.opacity = '0';
  document.body.appendChild(ta); ta.select();
  let ok = false;
  try { ok = document.execCommand('copy'); } catch (e) {}
  document.body.removeChild(ta);
  toast(ok ? '纯文本已复制\n笔记里 Ctrl+Shift+V 粘贴为纯文本' : '复制失败，请手动复制');
}
function copyImageAt(i) {
  const dataUrl = IMGDATA[i];
  if (!dataUrl) { toast('这张图没有本地缓存，右键另存即可'); return; }
  const img = new Image();
  img.onload = function () {
    const cv = document.createElement('canvas');
    cv.width = img.naturalWidth; cv.height = img.naturalHeight;
    cv.getContext('2d').drawImage(img, 0, 0);
    cv.toBlob(async function (blob) {
      try {
        await navigator.clipboard.write([new ClipboardItem({'image/png': blob})]);
        toast('图' + (i + 1) + ' 已复制\n去笔记里 Ctrl+V');
      } catch (e) { fallbackCopyHtml(dataUrl, i); }
    }, 'image/png');
  };
  img.onerror = function () { fallbackCopyHtml(dataUrl, i); };
  img.src = dataUrl;
}
function fallbackCopyHtml(dataUrl, i) {
  const box = document.createElement('div');
  box.style.position = 'fixed'; box.style.left = '-9999px';
  const im = document.createElement('img'); im.src = dataUrl;
  const p = document.createElement('p'); p.appendChild(im); box.appendChild(p);
  document.body.appendChild(box);
  const sel = window.getSelection(); const range = document.createRange();
  range.selectNodeContents(box); sel.removeAllRanges(); sel.addRange(range);
  let ok = false;
  try { ok = document.execCommand('copy'); } catch (e) {}
  sel.removeAllRanges(); document.body.removeChild(box);
  toast(ok ? '图' + (i + 1) + ' 已复制' : '复制失败，右键图片另存');
}
document.querySelectorAll('.imgbar button').forEach(btn => {
  btn.addEventListener('click', () => copyImageAt(+btn.dataset.idx));
});
document.getElementById('btnTop').addEventListener('click', copyAll);
document.getElementById('btnBottom').addEventListener('click', copyAll);
document.getElementById('btnPlain').addEventListener('click', copyPlain);
</script>
</body>
</html>
"""


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("json")
    ap.add_argument("--outdir", default=".")
    ap.add_argument("--name", default="作业")
    ap.add_argument("--no-cache", action="store_true", help="不下载图片，逐张复制按钮将不可用")
    ap.add_argument("--no-highlight", action="store_true", help="口令不加高亮底色")
    ap.add_argument("--hl-color", default=None, help="高亮色，形如 255,237,178 或 #FFEDB2")
    args = ap.parse_args()

    if args.no_highlight:
        HL["color"] = None
    elif args.hl_color:
        c = args.hl_color.strip().lstrip("#")
        if "," in c:
            HL["color"] = ",".join(x.strip() for x in c.split(","))
        else:
            HL["color"] = "%d,%d,%d" % (int(c[0:2], 16), int(c[2:4], 16), int(c[4:6], 16))

    with open(args.json, encoding="utf-8") as f:
        data = json.load(f)

    blocks = build_blocks(data)
    os.makedirs(args.outdir, exist_ok=True)

    md_path = os.path.join(args.outdir, args.name + ".md")
    with open(md_path, "w", encoding="utf-8") as f:
        f.write(to_md(blocks))

    txt_path = os.path.join(args.outdir, args.name + "_纯文本.txt")
    with open(txt_path, "w", encoding="utf-8") as f:
        f.write(to_plain(blocks))

    imgs = [b["src"] for b in blocks if b["type"] == "img"]
    imgdata = [None] * len(imgs)
    if not args.no_cache:
        for i, src in enumerate(imgs):
            imgdata[i] = fetch_and_encode(src)

    btns = "\n  ".join(
        '<button data-idx="%d">图%d</button>' % (i, i + 1) for i in range(len(imgs))
    ) or '<span class="lbl">（无图片）</span>'

    page = (PAGE
            .replace("__TITLE__", args.name)
            .replace("__IMGBTNS__", btns)
            .replace("__BODY__", to_html_sheet(blocks))
            .replace("__PLAIN__", json.dumps(to_plain(blocks), ensure_ascii=False))
            .replace("__IMGDATA__", json.dumps(imgdata, ensure_ascii=False)))

    html_path = os.path.join(args.outdir, args.name + "_微信笔记.html")
    with open(html_path, "w", encoding="utf-8") as f:
        f.write(page)

    ncodes = sum(len(v) if isinstance(v, list) else 1 for v in data.get("codes", {}).values())
    print("文字块：%d，图片：%d，口令：%d 条（来自 %d 个链接）" % (
        sum(1 for b in blocks if b["type"] == "text"), len(imgs),
        ncodes, len(data.get("codes", {}))))
    print("  " + md_path)
    print("  " + html_path)
    print("  " + txt_path)
    if data.get("warnings"):
        print("注意：")
        for w in data["warnings"]:
            print("  ! " + w)


if __name__ == "__main__":
    main()

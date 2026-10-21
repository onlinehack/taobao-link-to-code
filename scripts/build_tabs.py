#!/usr/bin/env python3
"""把「一个或多个」作业条目渲染成 Tab 版微信笔记页：一条作业一个 tab。

与 build.py 的差异只有排版组织方式：
- 每条作业一个 tab，各自独立的「复制全部 / 只复制文字 / 逐张复制图」；
- 传 2 条以上时额外给一个「全部」tab，用来一次性复制整份；
- 只有 1 条时自动隐藏 tab 栏，退回单页形态。

沿用 build.py 的硬约束：一行一个 <p>、正文图用远程 URL、逐张复制走 base64 图片剪贴板。

用法：
    python build_tabs.py all.json --outdir . --name 作业 [--header "大标题"]
    # all.json 形如 build.py 的 extract.json，或 [extract.json, ...] 数组
"""
import argparse
import json
import os
import sys

HERE = os.path.dirname(os.path.abspath(__file__))
sys.path.insert(0, HERE)
import build as bd  # noqa: E402


def plain_group(g):
    return "\n".join("\n".join(b["lines"]) for b in g if b["type"] == "text")


def html_group(g):
    """组内不插 <hr>：块之间的横线在 tab 形态下会显得很碎。"""
    return bd.to_html_sheet(g, hr=False)


def collect(data_list, header):
    """返回 [(tab名, [blocks...]), ...]"""
    groups = []
    for d in data_list:
        g = bd.build_blocks(d)
        if g:
            groups.append((d.get("label") or d.get("source") or "作业", g))

    panels = []
    if header and len(groups) > 1:
        panels.append(("全部", [[{"type": "text", "lines": [header]}]] + [g for _, g in groups]))
    panels += [(lab, [g]) for lab, g in groups]
    return panels


TEMPLATE = r"""<!DOCTYPE html>
<html lang="zh-CN">
<head>
<meta charset="UTF-8">
<meta name="viewport" content="width=device-width, initial-scale=1.0">
<title>__TITLE__ · 复制到微信笔记</title>
<style>
  * { margin:0; padding:0; box-sizing:border-box; }
  body {
    background:#f3f3f4; color:#1a1a1a; padding:14px 12px 96px;
    font-family:-apple-system,BlinkMacSystemFont,"PingFang SC","Microsoft YaHei",sans-serif;
    display:flex; flex-direction:column; align-items:center; gap:10px;
  }
  .wrap { width:100%; max-width:680px; }
  .hint { font-size:12px; color:#8a8a8a; line-height:1.6; margin-bottom:8px; }

  .tabs {
    width:100%; max-width:680px; display:flex; gap:6px; overflow-x:auto; padding-bottom:4px;
    scrollbar-width:none; -webkit-overflow-scrolling:touch;
  }
  .tabs::-webkit-scrollbar { display:none; }
  .tab {
    flex:0 0 auto; border:0; background:#fff; color:#555; font-family:inherit;
    font-size:12.5px; font-weight:600; padding:8px 13px; border-radius:16px;
    cursor:pointer; white-space:nowrap; box-shadow:0 1px 4px rgba(0,0,0,.06);
    max-width:190px; overflow:hidden; text-overflow:ellipsis;
  }
  .tab.on { background:#07c160; color:#fff; box-shadow:0 3px 12px rgba(7,193,96,.28); }
  .tabs.solo { display:none; }

  .panel { display:none; width:100%; max-width:680px; gap:10px; flex-direction:column; }
  .panel.on { display:flex; }

  .pbar {
    display:flex; align-items:center; justify-content:space-between; gap:10px; flex-wrap:wrap;
    background:#fff; border-radius:10px; padding:9px 12px; box-shadow:0 2px 10px rgba(0,0,0,.04);
  }
  .pbar .pname { font-size:12.5px; color:#666; font-weight:600; overflow:hidden; text-overflow:ellipsis; }
  .pbar .acts { display:flex; gap:8px; margin-left:auto; }
  .pbar button, .imgbar button {
    border:0; font-family:inherit; cursor:pointer; font-weight:600;
  }
  .pbar button { background:#07c160; color:#fff; font-size:13px; padding:7px 14px; border-radius:18px; }
  .pbar .ghost { background:#fff; color:#4a4a4a; box-shadow:inset 0 0 0 1px #e2e2e2; }

  .imgbar {
    background:#fff; border-radius:10px; padding:9px 12px; box-shadow:0 2px 10px rgba(0,0,0,.04);
    display:flex; align-items:center; gap:7px; flex-wrap:wrap;
  }
  .imgbar .lbl { font-size:12px; color:#8a8a8a; }
  .imgbar button { background:#eef7f1; color:#0a8f4d; font-size:12px; padding:5px 10px; border-radius:13px; }

  .sheet {
    background:#fff; border-radius:10px; padding:20px 18px 28px; box-shadow:0 3px 16px rgba(0,0,0,.05);
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

<div class="wrap"><div class="hint">__HINT__</div></div>

<div class="tabs__SOLO__" id="tabs">
__TABS__
</div>

__PANELS__

<div class="float"><button id="btnBottom">📋 复制当前页全部（含图片）</button></div>
<div class="toast" id="toast">已复制</div>

<script>
const PLAINS = __PLAINS__;
const IMGDATA = __IMGDATA__ ;
const toastEl = document.getElementById('toast');
let active = 0;

function toast(msg, ms) {
  toastEl.textContent = msg;
  toastEl.classList.add('show');
  clearTimeout(toastEl._h);
  toastEl._h = setTimeout(() => toastEl.classList.remove('show'), ms || 1900);
}
function showTab(i) {
  active = i;
  document.querySelectorAll('.tab').forEach((b, k) => b.classList.toggle('on', k === i));
  document.querySelectorAll('.panel').forEach((p, k) => p.classList.toggle('on', k === i));
  const on = document.querySelector('.tab.on');
  if (on && on.scrollIntoView) on.scrollIntoView({ block: 'nearest', inline: 'center', behavior: 'smooth' });
  window.scrollTo({ top: 0, behavior: 'smooth' });
}
function copyAll(i) {
  const ed = document.querySelector('.panel[data-i="' + i + '"] .sheet');
  if (!ed) return;
  const btn = document.querySelector('.panel[data-i="' + i + '"] [data-copy="' + i + '"]');
  const old = btn ? btn.textContent : '';
  if (btn) btn.textContent = '复制中…';
  const sel = window.getSelection();
  const range = document.createRange();
  range.selectNodeContents(ed);
  sel.removeAllRanges();
  sel.addRange(range);
  let ok = false;
  try { ok = document.execCommand('copy'); } catch (e) {}
  sel.removeAllRanges();
  if (btn) btn.textContent = old;
  toast(ok ? '已复制（含图片）\n去微信笔记里长按粘贴' : '复制失败：请手动 Ctrl+A / Ctrl+C');
}
function copyPlain(i) {
  const ta = document.createElement('textarea');
  ta.value = PLAINS[i] || '';
  ta.style.position = 'fixed'; ta.style.opacity = '0';
  document.body.appendChild(ta); ta.select();
  let ok = false;
  try { ok = document.execCommand('copy'); } catch (e) {}
  document.body.removeChild(ta);
  toast(ok ? '纯文本已复制\n笔记里 Ctrl+Shift+V 粘贴为纯文本' : '复制失败，请手动复制');
}
function copyImageAt(idx) {
  const dataUrl = IMGDATA[idx];
  if (!dataUrl) { toast('这张图没有本地缓存，右键另存即可'); return; }
  const img = new Image();
  img.onload = function () {
    const cv = document.createElement('canvas');
    cv.width = img.naturalWidth; cv.height = img.naturalHeight;
    cv.getContext('2d').drawImage(img, 0, 0);
    cv.toBlob(async function (blob) {
      try {
        await navigator.clipboard.write([new ClipboardItem({ 'image/png': blob })]);
        toast('这张图已复制\n去笔记里 Ctrl+V');
      } catch (e) { fallbackCopyHtml(dataUrl); }
    }, 'image/png');
  };
  img.onerror = function () { fallbackCopyHtml(dataUrl); };
  img.src = dataUrl;
}
function fallbackCopyHtml(dataUrl) {
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
  toast(ok ? '图片已复制' : '复制失败，右键图片另存');
}

document.addEventListener('click', function (e) {
  const t = e.target.closest('button');
  if (!t) return;
  if (t.dataset.tab !== undefined) showTab(+t.dataset.tab);
  else if (t.dataset.copy !== undefined) copyAll(+t.dataset.copy);
  else if (t.dataset.plain !== undefined) copyPlain(+t.dataset.plain);
  else if (t.dataset.idx !== undefined) copyImageAt(+t.dataset.idx);
});
document.getElementById('btnBottom').addEventListener('click', () => copyAll(active));
</script>
</body>
</html>
"""


def render_panel(i, name, groups, imgmap):
    blocks = [b for g in groups for b in g]
    imgs = [b["src"] for b in blocks if b["type"] == "img"]

    if imgs:
        btns = " ".join('<button data-idx="%d">图%d</button>' % (imgmap[s], n)
                        for n, s in enumerate(imgs, 1))
        imgbar = '<div class="imgbar"><span class="lbl">逐张复制：</span>%s</div>' % btns
    else:
        imgbar = ""

    body = "\n<hr>\n".join(html_group(g) for g in groups)
    return """<section class="panel%(cls)s" data-i="%(i)d">
  <div class="pbar">
    <span class="pname">%(name)s · %(n)d 图</span>
    <span class="acts">
      <button class="ghost" data-plain="%(i)d">只复制文字</button>
      <button data-copy="%(i)d">复制全部</button>
    </span>
  </div>
  %(imgbar)s
  <div class="sheet" data-editor="%(i)d" contenteditable="true" spellcheck="false">
%(body)s
  </div>
</section>""" % {"cls": " on" if i == 0 else "", "i": i, "name": name,
                  "n": len(imgs), "imgbar": imgbar, "body": body}


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("json", nargs="+", help="extract.json 或含多个作业的 all.json")
    ap.add_argument("--outdir", default=".")
    ap.add_argument("--name", default="作业")
    ap.add_argument("--header", default="", help="整份作业的大标题（有 2 条以上时才生成「全部」tab）")
    ap.add_argument("--maxw", type=int, default=720)
    ap.add_argument("--q", type=int, default=82)
    ap.add_argument("--no-highlight", action="store_true", help="口令不加高亮底色")
    ap.add_argument("--hl-color", default=None, help="高亮色，形如 255,237,178 或 #FFEDB2")
    args = ap.parse_args()

    if args.no_highlight:
        bd.HL["color"] = None
    elif args.hl_color:
        c = args.hl_color.strip().lstrip("#")
        if "," in c:
            bd.HL["color"] = ",".join(x.strip() for x in c.split(","))
        else:
            bd.HL["color"] = "%d,%d,%d" % (int(c[0:2], 16), int(c[2:4], 16), int(c[4:6], 16))

    data_list = []
    for p in args.json:
        with open(p, encoding="utf-8") as f:
            d = json.load(f)
        data_list.extend(d if isinstance(d, list) else [d])

    panels = collect(data_list, args.header)
    if not panels:
        sys.exit("没有可渲染的块")

    # 图片去重：同一 src 只编码一次，多个 tab 复用同一索引
    imgmap, imgdata = {}, []
    for _, groups in panels:
        for g in groups:
            for b in g:
                if b["type"] == "img" and b["src"] not in imgmap:
                    imgmap[b["src"]] = len(imgdata)
                    imgdata.append(None)

    for n, src in enumerate(list(imgmap), 1):
        imgdata[imgmap[src]] = bd.fetch_and_encode(src, max_w=args.maxw, quality=args.q)
        if len(imgmap) >= 10 and n % 10 == 0:
            print("  已缓存图片 %d/%d" % (n, len(imgmap)))

    # 「全部」tab 不编号，其余按顺序编号
    has_all = panels[0][0] == "全部" and len(panels) > 1
    labels = []
    for i, (name, _) in enumerate(panels):
        if has_all and i == 0:
            labels.append(name)
        else:
            labels.append("%d. %s" % (i if has_all else i + 1, name))
    tabs = "\n  ".join('<button class="tab%s" data-tab="%d">%s</button>' % (
        " on" if i == 0 else "", i, lab) for i, lab in enumerate(labels))

    body = "\n\n".join(render_panel(i, lab, gs, imgmap) for i, (lab, gs) in enumerate(panels))
    plains = ["\n\n".join(plain_group(g) for g in gs) for _, gs in panels]

    tip = "② 哪张图没进来，光标点到空位，点对应「图N」再粘一次（走图片剪贴板，必定成功）"
    hint = (("① 点「复制全部」→ 微信笔记粘贴；" if len(panels) == 1
             else "① 选一条作业 → 点「复制全部」→ 微信笔记粘贴；") + tip)

    page = (TEMPLATE
            .replace("__TITLE__", args.name)
            .replace("__HINT__", hint)
            .replace("__SOLO__", " solo" if len(panels) == 1 else "")
            .replace("__TABS__", tabs)
            .replace("__PANELS__", body)
            .replace("__PLAINS__", json.dumps(plains, ensure_ascii=False))
            .replace("__IMGDATA__", json.dumps(imgdata, ensure_ascii=False)))

    os.makedirs(args.outdir, exist_ok=True)
    html_path = os.path.join(args.outdir, args.name + "_微信笔记.html")
    with open(html_path, "w", encoding="utf-8") as f:
        f.write(page)

    print("tab：%d（%s）图片：%d（去重后编码 %d）" % (
        len(panels), "、".join(lab for lab in labels), sum(
            1 for _, gs in panels for g in gs for b in g if b["type"] == "img"), len(imgmap)))
    print("  " + html_path)
    print("  HTML 大小：%.1f MB" % (os.path.getsize(html_path) / 1048576))


if __name__ == "__main__":
    main()

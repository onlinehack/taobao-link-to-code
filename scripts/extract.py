#!/usr/bin/env python3
"""提取淘客作业页：按原始图文顺序拿到文案块 + 下钻子链接拿淘宝口令。

用法：
    python extract.py <url> [--out workdir] [--json out.json] [--max-depth 2] [--no-expand]

输出 JSON：
{
  "source": 入口 URL,
  "blocks": [{"type":"text","lines":[...],"soft":bool}, {"type":"img","src":"...","alt":"..."}],
  "codes": {"<url>": ["<code>", ...]},
  "items": [{"url","kind","depth","codes","title","img","error"}],
  "warnings": ["..."]
}

两种入口形态（可任意嵌套）：
  1) 口令页：页面里有 `$xxx$` / `8฿xxx)/ CA66` / `8￥…￥://`，直接取口令；
  2) 合集页（如 mlj94.com 的「图文合集」）：正文里又带子链接，递归下钻，
     子页内容**原地展开**成文案（`soft` 块表示「紧跟上一块、不加分隔线」）。
"""
import argparse
import glob
import json
import os
import re
import sys
from urllib.parse import parse_qs, unquote, urlparse

# ---------- 口令形态 ----------
CODE_DOLLAR = re.compile(r"\$[A-Za-z0-9]{6,24}\$")                       # 老口令 $xxxx$
CODE_BAO = re.compile(r"\d{0,2}฿[A-Za-z0-9]{4,24}\)\s*/\s*CA\d{2}")       # 淘礼金 / 红包 9฿xxx)/ CA38
CODE_CZ = re.compile(r"\d[￥¥][^￥¥\n]{2,40}[￥¥]://")                     # 新版 koulin 8￥ CZ0001 xxx￥://
CODE_LOOSE = re.compile(r"[$￥¥][^$￥¥\s]{4,40}[$￥¥]")                    # 兜底：$ ￥ ¥ 包裹
# 口令藏在跳转 URL 上的参数名（淘礼金页 token、红包页 tk）
CODE_PARAM_KEYS = ("token", "tk", "koulin", "coupon_token")

KOULIN_JS = r"""
() => {
  const e = document.getElementById('koulin');
  if (!e) return null;
  let s = (e.innerText || e.textContent || '').trim();
  // ( ) 与 ￥ 互转，统一成剪贴板里的形态
  s = s.replace(/[（(]/g, '￥').replace(/[）)]/g, '￥');
  return s;
}
"""
URL_RE = re.compile(r"https?://[A-Za-z0-9._~:/?#\[\]@!$&'()*+,;=%-]+")
MIN_IMG = 200          # 小于这个尺寸的图视为图标
SENDER_MIN_HITS = 3    # 同一短文本出现 >= 3 次，大概率是发件人昵称

# 页面模板自带的 UI 文案，不属于作业内容
NOISE = {
    "投诉", "复制链接", "复制文本", "加载中...", "加载中", "请输入显示的红色内容",
    "确定", "确 定", "取消", "关闭", "打开手机taobao", "一键复制", "长按框内", "复制", "全选",
    "点击图片放大查看", "搜索商品/品牌/关键词", "搜索商品",
}
NOISE_HINTS = ("长按框内", "全选", "打开手机taobao", "点击图片放大查看", "一键复制")

DUMP_JS = r"""
() => {
  const out = [];
  const blockish = ["p", "div", "li", "section", "h1", "h2", "h3", "h4", "tr", "article", "header", "footer"];
  const walk = (n) => {
    if (n.nodeType === 3) {
      const t = n.textContent.replace(/\s+/g, " ").trim();
      if (t) out.push({ t: "text", v: t });
      return;
    }
    if (n.nodeType !== 1) return;
    const tag = n.tagName.toLowerCase();
    if (tag === "script" || tag === "style" || tag === "noscript") return;
    if (tag === "img") {
      const src = n.currentSrc || n.src;
      const deco = /\.gif($|\?)/i.test(src) || src.includes("imgextra") || src.includes("kuaizhan");
      if (!deco && n.naturalWidth >= 200 && n.naturalHeight >= 200) {
        out.push({ t: "img", v: src, w: n.naturalWidth, h: n.naturalHeight });
      }
      return;
    }
    if (tag === "br") { out.push({ t: "br" }); return; }
    if (blockish.includes(tag)) {
      const txt = (n.textContent || "").replace(/\s+/g, "");
      const hasImg = n.querySelector("img");
      if (!txt && !hasImg) { out.push({ t: "blank" }); return; }
      for (const c of n.childNodes) walk(c);
      out.push({ t: "line" });
      return;
    }
    for (const c of n.childNodes) walk(c);
  };
  walk(document.body);
  return out;
}
"""


def find_chromium():
    """Playwright 版本与实际安装的 chromium 版本经常对不上，兜底找一个可用的。

    覆盖 Windows / macOS / Linux 三种放置方式，找不到就返回 None 交给 Playwright 自己处理。
    """
    bases = []
    env_base = os.environ.get("PLAYWRIGHT_BROWSERS_PATH")
    if env_base:
        bases.append(env_base)
    if os.name == "nt":
        bases.append(os.path.join(os.environ.get("LOCALAPPDATA", ""), "ms-playwright"))
    elif sys.platform == "darwin":
        bases.append(os.path.expanduser("~/Library/Caches/ms-playwright"))
    else:
        bases.append(os.path.expanduser("~/.cache/ms-playwright"))
        bases.append(os.path.expanduser("~/.local/share/ms-playwright"))

    patterns = [
        ("chrome-win64", "chrome.exe"),                          # Windows
        ("chrome-win", "chrome.exe"),                            # Windows（旧布局）
        ("chrome-mac", "Chromium.app/Contents/MacOS/Chromium"),  # macOS
        ("chrome-linux", "chrome"),                              # Linux
    ]
    for base in bases:
        for folder, exe in patterns:
            hits = sorted(glob.glob(os.path.join(base, "chromium-*", folder, exe)))
            if hits:
                return hits[-1]
    return None


async def open_page(ctx, url, wait_ms=5000):
    page = await ctx.new_page()
    try:
        await page.goto(url, wait_until="domcontentloaded", timeout=60000)
    except Exception:
        # 部分短链会先 302 到很慢的中转域，domcontentloaded 等不到；退一步用 commit 也要拿到页面
        await page.goto(url, wait_until="commit", timeout=90000)
    await page.wait_for_timeout(wait_ms)
    return page


def clean_line(s):
    return re.sub(r"\s+", "", s)


def is_noise(line):
    norm = clean_line(line).lower()
    if norm in {clean_line(x).lower() for x in NOISE}:
        return True
    if len(norm) <= 20 and any(h in norm for h in NOISE_HINTS):
        return True
    return False


# ---------------- 口令识别 ----------------

def norm_code(c):
    c = c.strip()
    c = re.sub(r"\)\s*/\s*", ")/ ", c)   # 9฿xxx)/   CA66 -> 9฿xxx)/ CA66
    return c


def find_codes(text):
    """按出现顺序去重收集一页里的口令。"""
    hits, spans = [], []
    for rx in (CODE_DOLLAR, CODE_BAO, CODE_CZ):
        for m in rx.finditer(text):
            hits.append((m.start(), norm_code(m.group(0))))
            spans.append((m.start(), m.end()))
    # 兜底：宽松包裹符。一页里可能混着两种形态（8￥…￥:// 与 ￥xxx￥），
    # 所以不是「没严格命中才跑」，而是始终跑一遍，只跳过与严格命中重叠的部分。
    # 要求中间有字母 + 至少 6 个字母数字，避免把「￥1630.00」当口令。
    for m in CODE_LOOSE.finditer(text):
        s, e = m.span()
        if any(a <= s < b or a < e <= b for a, b in spans):
            continue
        core = m.group(0)[1:-1]
        if re.search(r"[A-Za-z]", core) and len(re.findall(r"[A-Za-z0-9]", core)) >= 6:
            hits.append((s, norm_code(m.group(0))))
    hits.sort(key=lambda x: x[0])
    seen, out = set(), []
    for _, c in hits:
        if c and c not in seen:
            seen.add(c)
            out.append(c)
    return out


def codes_from_url(final_url):
    """淘礼金页 token / 红包页 tk 上的口令（页面本身没有文本口令时的唯一来源）。"""
    q = parse_qs(urlparse(final_url).query)
    for k in CODE_PARAM_KEYS:
        v = q.get(k, [None])[0]
        if not v:
            continue
        v = norm_code(unquote(v))
        if 6 <= len(v) <= 60 and re.search(r"[A-Za-z0-9]", v):
            return [v]
    return []


def pick_img(imgs):
    if not imgs:
        return None
    cand = [i for i in imgs if "kuaizhan" not in i["src"].lower()]
    if not cand:
        cand = imgs
    # 商品主图一般来自 alicdn 的 bao/uploaded
    cand.sort(key=lambda i: (0 if "bao/uploaded" in i["src"] else 1, -(i["w"] * i["h"])))
    return cand[0]["src"]


def split_blocks(seq):
    """把原始序列切成「一条消息 = 一个块」：图片单独成块，重复的昵称行作为消息分隔。"""
    counts = {}
    for it in seq:
        if it["t"] == "text" and len(it["v"]) <= 6:
            counts[it["v"]] = counts.get(it["v"], 0) + 1
    senders = {k for k, v in counts.items() if v >= SENDER_MIN_HITS}

    blocks, cur, buf = [], [], ""

    def flush_block():
        nonlocal cur, buf
        buf = ""
        while cur and cur[-1] == "":
            cur.pop()
        while cur and cur[0] == "":
            cur.pop(0)
        if cur:
            blocks.append({"type": "text", "lines": cur})
        cur = []

    for it in seq:
        if it["t"] == "text":
            buf = (buf + " " + it["v"]).strip()
            continue
        if it["t"] == "img":
            if buf:
                cur.append(buf)
                buf = ""
            flush_block()
            blocks.append({"type": "img", "src": it["v"], "alt": ""})
            continue
        if it["t"] in ("br", "line"):
            if buf:
                if is_noise(buf):
                    buf = ""
                    continue
                if buf in senders:
                    buf = ""
                    flush_block()
                    continue
                cur.append(buf)
                buf = ""
            continue
        if it["t"] == "blank":
            if buf:
                cur.append(buf)
                buf = ""
            if cur and cur[-1] != "":
                cur.append("")
            continue
    if buf:
        if not is_noise(buf):
            cur.append(buf)
    flush_block()
    return blocks


def trim_join(parts):
    """把相邻文字块的末尾/开头空行去掉，避免展开后多出空段。"""
    out = []
    for b in parts:
        if b["type"] == "img":
            out.append(b)
            continue
        lines = list(b["lines"])
        while lines and lines[0] == "":
            lines.pop(0)
        while lines and lines[-1] == "":
            lines.pop()
        if lines:
            out.append({"type": "text", "lines": lines, "soft": b.get("soft", False)})
    if out:
        out[0]["soft"] = False
    return out


def sub_urls_of(lines, self_url):
    urls = []
    for ln in lines:
        for u in URL_RE.findall(ln):
            u = u.rstrip(")，,。.、;；:：!！")
            if u != self_url and u not in urls and not u.startswith("data:"):
                urls.append(u)
    return urls


# 一链多口令（礼金 + 优惠券）时，每条口令上面要有「它是什么」，否则两个裸口令看不出用途
CIRCLED_BTN = re.compile(r"^[①②③④⑤⑥⑦⑧⑨]\s*\S")
HINT_WORD = re.compile(r"领取\s*[「【]?(礼金|红包|优惠券|消费券)")


def code_labels(lines, codes):
    """按顺序给每条口令配一句说明。只有 1 条口令时不需要（返回全 None）。

    优先用页面自己的按钮文案（① 先领补贴礼金 / ② 再领优惠券），
    数量对不上时退回「打开淘宝即可领取X」这句提示。
    """
    n = len(codes)
    if n < 2:
        return [None] * n
    btns = [ln.strip() for ln in lines
            if CIRCLED_BTN.match(ln.strip()) and len(ln.strip()) <= 18]
    if len(btns) == n:
        return btns
    labels = []
    for c in codes:
        label = None
        idx = next((i for i, ln in enumerate(lines) if c in ln), None)
        if idx is not None:
            for ln in lines[idx:idx + 3]:
                m = HINT_WORD.search(ln)
                if m:
                    label = "补贴礼金" if m.group(1) in ("礼金", "红包") else m.group(1)
                    break
        labels.append(label)
    return labels


def expand_blocks(blocks, childmap):
    """把合集页正文里的子链接就地换成：{口令行} 或 {子页完整内容}。

    soft=True 表示「跟着上一块排版、不插分隔线」，用于展开进来的子页内容。
    """
    keys = sorted(childmap, key=len, reverse=True)   # 长 URL 先匹配，避免前缀误伤
    out = []

    def emission(b, soft):
        b = dict(b)
        b["soft"] = soft
        out.append(b)

    for b in blocks:
        if b["type"] == "img":
            emission({"type": "img", "src": b["src"], "alt": b.get("alt", "")}, False)
            continue
        cur = []
        first_flush = [True]

        def flush():
            if cur:
                emission({"type": "text", "lines": list(cur)}, not first_flush[0])
                first_flush[0] = False
                cur.clear()

        for ln in b["lines"]:
            hit = next((u for u in keys if u in ln), None)
            if not hit:
                cur.append(ln)
                continue
            ch = childmap[hit]
            if ch["kind"] == "collection" and ch["blocks"]:
                flush()
                for cb in ch["blocks"]:
                    emission({k: v for k, v in cb.items() if k != "soft"}, True)
                first_flush[0] = False
            elif ch["codes"]:
                # 一行一个口令：原行只有链接时，把 N 个口令拆成 N 行（凑单页常有两步口令）；
                # 原行还有别的文字时，口令内联进去，避免把一句话拆散
                if ln.strip() == hit:
                    labels = ch.get("labels") or []
                    for i, c in enumerate(ch["codes"]):
                        lb = labels[i] if i < len(labels) else None
                        if lb:
                            cur.append(lb)      # 先说明「这条口令干什么」，再给口令
                        cur.append(c)
                else:
                    cur.append(ln.replace(hit, " ".join(ch["codes"])))
            else:
                cur.append(ln)   # 拿不到口令就保留原链接，不硬删
        flush()
    return trim_join(out)


class Resolver:
    """递归下钻：口令页 -> codes；合集页 -> 把子页内容展开进 blocks。"""

    def __init__(self, ctx, wait_ms=5000, max_depth=2, expand=True):
        self.ctx = ctx
        self.wait_ms = wait_ms
        self.max_depth = max_depth
        self.expand = expand
        self.cache = {}
        self.order = []
        self.warnings = []

    def warn(self, msg):
        if msg not in self.warnings:
            self.warnings.append(msg)

    async def node(self, url, depth=0):
        if url in self.cache:
            return self.cache[url]

        node = {"url": url, "depth": depth, "kind": "code", "codes": [],
                "blocks": [], "title": "", "img": None, "error": None, "final": url}
        self.cache[url] = node            # 先占位，避免 A->B->A 的环
        self.order.append(node)

        page = await open_page(self.ctx, url, self.wait_ms)
        try:
            seq = await page.evaluate(DUMP_JS)
            try:
                koulin = await page.evaluate(KOULIN_JS)
            except Exception:
                koulin = None
            node["final"] = page.url
        finally:
            await page.close()

        blocks = split_blocks(seq)
        node["blocks"] = blocks
        lines = [ln for b in blocks if b["type"] == "text" for ln in b["lines"]]
        node["title"] = lines[0] if lines else ""
        imgs = [{"src": it["v"], "w": it.get("w", 0), "h": it.get("h", 0)}
                for it in seq if it["t"] == "img"]
        node["img"] = pick_img(imgs)

        body = "\n".join(lines)
        codes = find_codes(body)
        if koulin:
            koulin = norm_code(koulin)
            if koulin not in codes:
                codes.insert(0, koulin)
        if not codes:
            codes = codes_from_url(node["final"])
        node["codes"] = codes
        node["labels"] = code_labels(lines, codes)

        urls = sub_urls_of(lines, url)
        node["sub_urls"] = urls

        if urls and self.expand and depth < self.max_depth:
            node["kind"] = "collection"
            childmap = {}
            for u in urls:
                childmap[u] = await self.node(u, depth + 1)
            node["blocks"] = expand_blocks(blocks, childmap)
            node["codes"] = []            # 合集页自身不产出口令，口令都在子页
        elif urls:
            node["kind"] = "collection_flat"
            if not self.expand:
                self.warn("%s 是合集页，--no-expand 已关闭展开，子链接保持原样" % url)
            else:
                self.warn("%s 是合集页，但已达展开层数上限（%d），子链接保持原样"
                          % (url, self.max_depth))
        return node


async def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("url")
    ap.add_argument("--out", default=".", help="工作目录（默认当前目录）")
    ap.add_argument("--json", default="extract.json")
    ap.add_argument("--wait", type=int, default=5000)
    ap.add_argument("--max-depth", type=int, default=2,
                    help="下钻层数：0=只看入口页，1=展开一层合集，2=展开两层（默认）")
    ap.add_argument("--no-expand", action="store_true",
                    help="合集子页不展开，只把子链接换成口令")
    args = ap.parse_args()

    try:
        from playwright.async_api import async_playwright
    except ImportError:
        sys.exit("缺少 playwright：先执行 pip install playwright && playwright install chromium")

    os.makedirs(args.out, exist_ok=True)

    ua = ("Mozilla/5.0 (iPhone; CPU iPhone OS 17_0 like Mac OS X) "
          "AppleWebKit/605.1.15 (KHTML, like Gecko) Version/17.0 Mobile/15E148 Safari/604.1")

    async with async_playwright() as p:
        launch_kw = {"headless": True}
        try:
            browser = await p.chromium.launch(**launch_kw)
        except Exception:
            exe = find_chromium()
            if not exe:
                raise
            browser = await p.chromium.launch(executable_path=exe, **launch_kw)
        ctx = await browser.new_context(
            viewport={"width": 480, "height": 1200},
            user_agent=ua, is_mobile=True, has_touch=True,
        )

        resolver = Resolver(ctx, args.wait, args.max_depth, expand=not args.no_expand)
        entry = await resolver.node(args.url, 0)

        if entry["kind"] == "code" and not entry["codes"]:
            resolver.warn("%s 没找到口令（页面里没有 $…$ / ฿…)/ CA… / token 参数）" % args.url)

        # 清掉占位用的空节点
        nodes = [n for n in resolver.order]
        for n in nodes:
            if n["error"]:
                resolver.warn("%s 打开失败：%s" % (n["url"], n["error"]))

        await browser.close()

    blocks = entry["blocks"]
    # 给图片块补 alt（用对应商品的标题）
    for b in blocks:
        if b["type"] == "img":
            for n in nodes:
                if n["img"] == b["src"]:
                    b["alt"] = n["title"]

    codes = {n["url"]: n["codes"] for n in nodes if n["codes"]}
    items = [{"url": n["url"], "kind": n["kind"], "depth": n["depth"], "codes": n["codes"],
              "labels": n.get("labels") or [],
              "code": (n["codes"][0] if n["codes"] else None),
              "title": n["title"], "img": n["img"], "error": n["error"]} for n in nodes]

    data = {
        "source": args.url,
        "blocks": blocks,
        "codes": codes,
        "items": items,
        "warnings": resolver.warnings,
    }
    path = os.path.join(args.out, args.json)
    with open(path, "w", encoding="utf-8") as f:
        json.dump(data, f, ensure_ascii=False, indent=1)

    print("块数：%d（文字 %d / 图片 %d）" % (
        len(blocks),
        sum(1 for b in blocks if b["type"] == "text"),
        sum(1 for b in blocks if b["type"] == "img"),
    ))
    print("页面：%d，拿到口令：%d 条" % (len(nodes), sum(len(n["codes"]) for n in nodes)))
    for n in nodes:
        tag = {"collection": "合集", "collection_flat": "合集(未展开)", "code": "口令页"}[n["kind"]]
        print("  %s%s %-46s %s %s" % (
            "  " * n["depth"], "└" if n["depth"] else "•", n["url"], tag,
            " | ".join(n["codes"]) or (n["error"] or "-")))
    for w in resolver.warnings:
        print("  ! " + w)
    print("已写入 " + path)


if __name__ == "__main__":
    import asyncio
    asyncio.run(main())

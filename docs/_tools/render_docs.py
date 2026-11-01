"""Render the design documents locally; this is documentation tooling, not the app."""
from pathlib import Path
from html import escape
from urllib.parse import urlsplit, unquote
import json
import re

from bs4 import BeautifulSoup
from markdown_it import MarkdownIt

ROOT = Path(__file__).resolve().parents[1]
PAGES = [
    ("README.md", "index.html", "阅读入口"),
    ("01-system-design.md", "01-system-design.html", "01 系统设计"),
    ("02-evaluation.md", "02-evaluation.html", "02 评估设计"),
    ("03-defense-and-extensions.md", "03-defense-and-extensions.html", "03 答辩与扩展"),
    ("04-prompts-and-contracts.md", "04-prompts-and-contracts.html", "04 提示词与契约"),
    ("implementation-status.md", "implementation-status.html", "实际实现状态"),
    ("evaluation-report.md", "evaluation-report.html", "真实评测报告"),
    ("delivery-evidence.md", "delivery-evidence.html", "集成交付证据"),
    ("engineering-decisions.md", "engineering-decisions.html", "七项工程裁定"),
]
LINK_MAP = {source: target for source, target, _ in PAGES}
CSS = """
:root { color-scheme: light; --ink:#172638; --muted:#5b6877; --line:#dce3eb; --accent:#225cc5; }
* { box-sizing:border-box; }
html { scroll-behavior:smooth; scroll-padding-top:24px; }
body { margin:0; color:var(--ink); background:#f5f7fb; font:16px/1.85 'Microsoft YaHei','PingFang SC',sans-serif; }
a { color:var(--accent); text-decoration:none; overflow-wrap:anywhere; }
a:hover { text-decoration:underline; }
img { max-width:100%; height:auto; }
.sidebar { position:fixed; inset:0 auto 0 0; width:268px; padding:30px 22px; background:#edf2f9; border-right:1px solid var(--line); overflow:auto; }
.brand { font-size:18px; font-weight:700; line-height:1.6; margin-bottom:6px; }
.meta { color:var(--muted); font-size:12px; }
.pages { display:grid; gap:4px; margin:22px 0; }
.pages a { display:block; padding:7px 10px; border-radius:6px; }
.pages a[aria-current=page] { background:#dce8fc; color:#19438c; font-weight:700; }
.toc { border-top:1px solid var(--line); padding-top:14px; font-size:13px; }
.toc summary { cursor:pointer; font-weight:700; margin-bottom:10px; }
.toc a { display:block; color:#48566b; padding:5px 2px; line-height:1.6; }
main { margin-left:268px; padding:38px 42px 72px; }
article { max-width:1100px; margin:auto; background:white; border:1px solid var(--line); border-radius:12px; padding:40px 46px; min-width:0; }
.status { display:inline-block; background:#eaf2ff; color:#24549c; padding:3px 10px; border-radius:5px; font-size:12px; letter-spacing:.04em; margin-bottom:12px; }
h1 { font-size:32px; line-height:1.4; letter-spacing:-.025em; margin:12px 0 25px; }
h2 { font-size:24px; line-height:1.5; margin:44px 0 18px; border-top:1px solid var(--line); padding-top:25px; }
h3 { font-size:19px; line-height:1.6; margin:29px 0 12px; }
p { margin:14px 0; overflow-wrap:anywhere; }
li { margin:7px 0; }
ul,ol { padding-left:1.65em; }
.table-wrap { overflow-x:auto; margin:22px 0; border:1px solid var(--line); border-radius:7px; }
table { width:100%; border-collapse:collapse; font-size:14px; line-height:1.75; }
th,td { vertical-align:top; text-align:left; padding:11px 13px; border-bottom:1px solid var(--line); overflow-wrap:anywhere; min-width:105px; }
th { background:#edf3fc; color:#244466; font-weight:700; }
tr:last-child td { border-bottom:0; }
tbody tr:nth-child(even) { background:#fafbfd; }
pre { border:1px solid #dce3ee; border-radius:8px; padding:19px; background:#f4f7fb; line-height:1.65; font-size:13px; overflow:auto; }
code { font-family:Consolas,'Microsoft YaHei',monospace; }
p code,li code { background:#f1f4f9; padding:2px 4px; border-radius:3px; }
blockquote { border-left:3px solid #9ab9ec; margin-left:0; padding-left:18px; color:#435269; }
footer { border-top:1px solid var(--line); margin-top:40px; padding-top:18px; color:var(--muted); font-size:12px; }
@media(max-width:1050px) {
  .sidebar { position:static; width:auto; padding:20px 22px; border-right:0; border-bottom:1px solid var(--line); }
  .pages { display:flex; flex-wrap:wrap; gap:5px; margin:12px 0; font-size:14px; }
  .toc { display:none; }
  main { margin-left:0; padding:20px; }
  article { padding:28px; }
}
@media(max-width:600px) {
  body { font-size:15px; }
  main { padding:10px; }
  article { padding:23px 18px; border-radius:8px; }
  h1 { font-size:26px; }
  h2 { font-size:21px; }
  table { min-width:620px; }
  pre { font-size:12px; padding:13px; }
}
@media print {
  .sidebar,.status { display:none; }
  body { background:white; font-size:10pt; }
  main { margin:0; padding:0; }
  article { max-width:none; border:0; padding:0; }
  h2,h3 { break-after:avoid; }
  tr { break-inside:avoid; }
  .table-wrap { overflow:visible; }
  pre { white-space:pre-wrap; overflow-wrap:anywhere; }
  a { color:inherit; }
}
"""


def render():
    parser = MarkdownIt("commonmark", {"html": False}).enable("table")
    for source, target, label in PAGES:
        content = (ROOT / source).read_text(encoding="utf-8")
        soup = BeautifulSoup(parser.render(content), "html.parser")
        headings = soup.find_all(["h1", "h2", "h3"])
        for i, heading in enumerate(headings, 1):
            heading["id"] = f"section-{i}"
        title = headings[0].get_text()
        toc = "".join(f'<a href="#{h["id"]}">{escape(h.get_text())}</a>' for h in headings if h.name == "h2")
        for anchor in soup.find_all("a", href=True):
            anchor["href"] = LINK_MAP.get(anchor["href"], anchor["href"])
        for table in soup.find_all("table"):
            wrapper = soup.new_tag("div", attrs={"class": "table-wrap", "tabindex": "0", "role": "region", "aria-label": "可横向滚动的表格"})
            table.wrap(wrapper)
        navigation = "".join(
            f'<a href="{t}"' + (' aria-current="page"' if t == target else '') + f'>{escape(l)}</a>'
            for _, t, l in PAGES
        )
        result = f'''<!doctype html>
<html lang="zh-CN"><head><meta charset="utf-8"><meta name="viewport" content="width=device-width,initial-scale=1">
<title>{escape(title)}</title><style>{CSS}</style></head><body>
<aside class="sidebar"><div class="brand">IT Helpdesk Agent</div><div class="meta">方案设计 · 2026.11.02</div>
<nav class="pages" aria-label="文档导航">{navigation}</nav><details class="toc" open><summary>本页目录</summary>{toc}</details></aside>
<main><article><div class="status">设计与实测分开 · <a href="implementation-status.html">实现状态</a> · <a href="evaluation-report.html">真实评测</a> · <a href="delivery-evidence.html">集成证据</a></div>{soup}
<footer>本页来自 <a href="{source}">{source}</a>。本文区分已确认选择、工程默认值与未来扩展；评估方案不代表已取得的成绩。</footer>
</article></main></body></html>'''
        (ROOT / target).write_text(result, encoding="utf-8")


def verify():
    report = []
    for source, target, _ in PAGES:
        markdown = (ROOT / source).read_text(encoding="utf-8")
        soup = BeautifulSoup((ROOT / target).read_text(encoding="utf-8"), "html.parser")
        assert '\ufffd' not in markdown, f"Replacement character in {source}"
        assert len(soup.find_all("h1")) == 1, target
        ids = [e["id"] for e in soup.select("[id]")]
        assert len(ids) == len(set(ids)), f"Duplicate IDs in {target}"
        for a in soup.find_all("a", href=True):
            link = urlsplit(a["href"])
            if link.scheme or link.netloc:
                continue
            destination = ROOT / unquote(link.path) if link.path else ROOT / target
            assert destination.exists(), f"Broken link {target}: {a['href']}"
            if link.fragment:
                linked = BeautifulSoup(destination.read_text(encoding="utf-8"), "html.parser")
                assert linked.find(id=unquote(link.fragment)), a["href"]
        for table in soup.find_all("table"):
            widths = [len(row.find_all(["td", "th"], recursive=False)) for row in table.find_all("tr")]
            assert len(set(widths)) == 1, f"Inconsistent table cells in {target}"
        json_examples = 0
        for code in soup.select("pre code"):
            text = code.get_text().strip()
            if text.startswith("{"):
                json.loads(text)
                json_examples += 1
        report.append({"file": target, "headings": len(soup.find_all(["h1", "h2", "h3"])), "tables": len(soup.find_all("table")), "valid_json_examples": json_examples, "source_characters": len(markdown)})
    evaluation = (ROOT / "02-evaluation.md").read_text(encoding="utf-8")
    cases = re.findall(r"^\| ([DH]\d{2}) \|", evaluation, re.M)
    expected = [f"D{i:02}" for i in range(1, 21)] + [f"H{i:02}" for i in range(1, 11)]
    assert cases == expected, f"Incorrect scenario inventory: {cases}"
    print(json.dumps({"documents": report, "formal_scenario_blueprints": len(cases), "status": "document checks passed; no application or model tests run"}, ensure_ascii=False, indent=2))


if __name__ == "__main__":
    render()
    verify()

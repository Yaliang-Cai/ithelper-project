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
    ("README.md", "index.html", "文档"),
    ("demo.md", "demo.html", "演示"),
    ("delivery-iteration-result.md", "delivery-iteration-result.html", "员工交互迭代"),
    ("01-system-design.md", "01-system-design.html", "系统设计"),
    ("02-evaluation.md", "02-evaluation.html", "02 评估设计"),
    ("03-defense-and-extensions.md", "03-defense-and-extensions.html", "03 答辩与扩展"),
    ("04-prompts-and-contracts.md", "04-prompts-and-contracts.html", "04 提示词与契约"),
    ("implementation-status.md", "implementation-status.html", "验证结果"),
    ("evaluation-report.md", "evaluation-report.html", "真实评测报告"),
    ("delivery-evidence.md", "delivery-evidence.html", "集成交付证据"),
    ("engineering-decisions.md", "engineering-decisions.html", "七项工程裁定"),
]
LINK_MAP = {source: target for source, target, _ in PAGES}
CSS = """
:root { color-scheme:light; }
* { box-sizing:border-box; }
body { max-width:980px; margin:32px auto; padding:0 24px; color:#252525; background:#fff; font:16px/1.8 system-ui,'Microsoft YaHei',sans-serif; }
a { color:#27567c; text-underline-offset:3px; overflow-wrap:anywhere; }
header { border-bottom:1px solid #ddd; padding-bottom:16px; margin-bottom:32px; }
.brand { color:inherit; font-weight:650; text-decoration:none; }
.pages { display:flex; flex-wrap:wrap; gap:8px 24px; margin-top:12px; font-size:14px; }
.pages a[aria-current=page] { color:inherit; font-weight:650; }
.toc { font-size:14px; margin:20px 0; }
.toc summary { cursor:pointer; }
.toc a { display:block; margin:4px 0; }
h1 { font-size:28px; line-height:1.4; margin:24px 0; }
h2 { font-size:21px; margin:32px 0 12px; }
h3 { font-size:18px; margin:24px 0 10px; }
p,li { overflow-wrap:anywhere; }
li { margin:6px 0; }
img { max-width:100%; height:auto; }
.table-wrap { overflow-x:auto; margin:20px 0; }
table { width:100%; border-collapse:collapse; font-size:14px; line-height:1.65; }
th,td { text-align:left; vertical-align:top; border-bottom:1px solid #ddd; padding:10px; min-width:100px; }
th { background:#f5f5f5; }
pre { padding:16px; background:#f6f6f6; overflow:auto; font-size:13px; line-height:1.6; }
code { font-family:Consolas,'Microsoft YaHei',monospace; }
blockquote { margin-left:0; padding-left:16px; border-left:2px solid #bbb; color:#555; }
footer { margin:36px 0 24px; padding-top:14px; border-top:1px solid #ddd; color:#666; font-size:13px; }
@media(max-width:600px) { body { padding:0 16px; margin:20px auto; font-size:15px; } h1 { font-size:25px; } table { min-width:600px; } }
@media print { header,.toc { display:none; } body { max-width:none; font-size:10pt; margin:0; } h2,h3 { break-after:avoid; } tr { break-inside:avoid; } pre { white-space:pre-wrap; overflow-wrap:anywhere; } .table-wrap { overflow:visible; } }
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
            for _, t, l in PAGES if t in ("index.html", "demo.html", "implementation-status.html", "01-system-design.html")
        )
        result = f'''<!doctype html>
<html lang="zh-CN"><head><meta charset="utf-8"><meta name="viewport" content="width=device-width,initial-scale=1">
<title>{escape(title)}</title><style>{CSS}</style></head><body>
<header><a class="brand" href="../README.md">IT Helpdesk Agent</a>
<nav class="pages" aria-label="文档导航">{navigation}</nav></header>
<main><article><details class="toc"><summary>本页目录</summary>{toc}</details>{soup}
<footer><a href="{source}">Markdown 原文</a></footer>
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

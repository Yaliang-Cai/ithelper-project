"""Build portable HTML reading copies of the submission Markdown files."""
from pathlib import Path
from html import escape
from urllib.parse import urlsplit, unquote
import json
import os

from bs4 import BeautifulSoup
from markdown_it import MarkdownIt

ROOT=Path(__file__).resolve().parents[2]
PAGES=[('README.md','delivery.html')]+[(f'docs/{name}.md',f'docs/{name}.html') for name in (
    '01-system-design','02-evaluation','implementation-status',
    'productionization','evaluation-report','delivery-iteration-result','delivery-evidence')]
TARGETS={(ROOT/source).resolve():ROOT/target for source,target in PAGES}
CSS='''
*{box-sizing:border-box}body{max-width:940px;margin:36px auto;padding:0 24px;color:#222;background:#fff;font:16px/1.8 system-ui,'Microsoft YaHei',sans-serif}
h1{font-size:28px;line-height:1.4;margin:20px 0}h2{font-size:21px;margin:30px 0 12px}h3{font-size:18px}
a{color:#285e88;text-underline-offset:3px;overflow-wrap:anywhere}p,li{overflow-wrap:anywhere}li{margin:5px 0}
.table-wrap{overflow:auto;margin:18px 0}table{border-collapse:collapse;width:100%;font-size:14px;line-height:1.6}th,td{text-align:left;vertical-align:top;padding:10px;border-bottom:1px solid #ddd;min-width:105px}th{background:#f5f5f5}
pre{overflow:auto;padding:16px;background:#f6f6f6;font-size:13px;line-height:1.6}code{font-family:Consolas,monospace}footer{border-top:1px solid #ddd;margin-top:32px;padding-top:12px;font-size:14px}
@media(max-width:600px){body{padding:0 16px;margin:20px auto;font-size:15px}h1{font-size:25px}table{min-width:560px}}
@media print{body{max-width:none;margin:0;font-size:10pt}h2,h3{break-after:avoid}tr{break-inside:avoid}pre{white-space:pre-wrap;overflow-wrap:anywhere}.table-wrap{overflow:visible}footer{display:none}}
'''

def render():
    parser=MarkdownIt('commonmark',{'html':False}).enable('table')
    for source,target in PAGES:
        src=ROOT/source;dst=ROOT/target
        soup=BeautifulSoup(parser.render(src.read_text(encoding='utf-8')),'html.parser')
        for index,heading in enumerate(soup.find_all(['h1','h2','h3']),1):heading['id']=f'section-{index}'
        for anchor in soup.find_all('a',href=True):
            link=urlsplit(anchor['href'])
            if link.scheme or link.netloc or not link.path:continue
            original=(src.parent/unquote(link.path)).resolve()
            final=TARGETS.get(original,original)
            anchor['href']=os.path.relpath(final,dst.parent).replace('\\','/')+('#'+link.fragment if link.fragment else '')
        for table in soup.find_all('table'):
            table.wrap(soup.new_tag('div',attrs={'class':'table-wrap','tabindex':'0'}))
        home=os.path.relpath(ROOT/'delivery.html',dst.parent).replace('\\','/')
        markdown=os.path.relpath(src,dst.parent).replace('\\','/')
        title=escape(soup.h1.get_text())
        dst.write_text(f'''<!doctype html>
<html lang="zh-CN"><head><meta charset="utf-8"><meta name="viewport" content="width=device-width,initial-scale=1">
<title>{title}</title><style>{CSS}</style></head><body><main>{soup}</main>
<footer><a href="{home}">项目说明</a> · <a href="{markdown}">Markdown</a></footer></body></html>''',encoding='utf-8')

def verify():
    for _,target in PAGES:
        page=ROOT/target;soup=BeautifulSoup(page.read_text(encoding='utf-8'),'html.parser')
        assert len(soup.find_all('h1'))==1,target
        assert '\ufffd' not in soup.get_text(),target
        for anchor in soup.find_all('a',href=True):
            link=urlsplit(anchor['href'])
            if link.scheme or link.netloc:continue
            destination=(page.parent/unquote(link.path)).resolve() if link.path else page
            assert destination.exists(),(target,anchor['href'])
        for table in soup.find_all('table'):
            widths=[len(row.find_all(['td','th'],recursive=False)) for row in table.find_all('tr')]
            assert len(set(widths))==1,(target,widths)
    print(json.dumps({'pages':len(PAGES),'local_links':'valid','tables':'valid'},ensure_ascii=False))

if __name__=='__main__':
    render()
    verify()

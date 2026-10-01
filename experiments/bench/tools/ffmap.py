"""Which of FormFactory's gold keys name no field of their form by its label: how formfactory.ALIASES was made.

    python3 experiments/bench/tools/ffmap.py
"""
import json,re,glob,os,sys
from pathlib import Path
sys.path.insert(0, str(Path(__file__).resolve().parent.parent))  # experiments/bench/, for paths
import paths  # noqa: E402
os.chdir(paths.DATA / "formfactory")  # its templates/ and data/ are read from there
from html.parser import HTMLParser
class P(HTMLParser):
    def __init__(s):
        super().__init__(); s.labels={}; s.cur=None; s.buf=''; s.fields=[]; s.lab_stack=[]
    def handle_starttag(s,tag,a):
        a=dict(a)
        if tag=='label': s.cur=a.get('for'); s.buf=''; s.lab_stack.append(len(s.fields))
        if tag in('input','select','textarea') and a.get('type') not in ('submit','button','hidden'):
            s.fields.append(dict(tag=tag,type=a.get('type'),name=a.get('name'),id=a.get('id'),value=a.get('value')))
    def handle_endtag(s,tag):
        if tag=='label':
            txt=re.sub(r'\s+',' ',s.buf).strip().rstrip('*').strip()
            if s.cur: s.labels[s.cur]=txt
            else:
                i=s.lab_stack[-1] if s.lab_stack else None
                if i is not None and i < len(s.fields): s.fields[i].setdefault('wrap',txt)
            s.cur=None
            if s.lab_stack: s.lab_stack.pop()
    def handle_data(s,d):
        if s.cur is not None or s.lab_stack: s.buf+=d
def norm(x): return re.sub(r'[^a-z0-9]','',x.lower())
temps={}
for t in sorted(glob.glob('templates/[A-H]1*.html')):
    p=P(); p.feed(open(t).read())
    labs={}
    for f in p.fields:
        l=p.labels.get(f['id']) or f.get('wrap')
        if l: labs.setdefault(norm(l),[]).append(f)
    temps[os.path.basename(t)[:3]]=(p,labs)
for d in sorted(glob.glob('data/data1/*.json')):
    g=json.load(open(d)); keys=set().union(*[x.keys() for x in g])
    best=max(temps, key=lambda t: sum(norm(k) in temps[t][1] for k in keys))
    hit=[k for k in keys if norm(k) in temps[best][1]]
    miss=[k for k in keys if norm(k) not in temps[best][1]]
    print(os.path.basename(d)[:-5], '->', best, '%d/%d'%(len(hit),len(keys)), 'MISS', miss[:6])

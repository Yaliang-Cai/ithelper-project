"""BM25S/Jieba + BGE-M3 RRF retrieval over reviewed synthetic sources."""
from contextlib import closing
import hashlib
import json
import logging
from pathlib import Path
import re
import sqlite3

import bm25s
import jieba
import numpy as np

from app.seed import DATA_DIR, WORLD_AS_OF, knowledge_documents

MODEL='BAAI/bge-m3'
DIMENSION=1024
INDEX_VERSION='rrf-v2'
CHUNK_VERSION='whole-reviewed-section-v1-300-600'
# Initial five-query development probe: relevant Chinese queries top cosine
# .528-.643, unrelated probes .253/.407. This is a candidate gate, not a
# calibrated correctness probability; broader evaluation must revisit it.
SEMANTIC_MIN_COSINE=.48
jieba.setLogLevel(logging.ERROR)


def tokenize(text):
    # Keep product names, error codes and dotted versions intact.
    parts=re.findall(r'[A-Za-z0-9_.-]+|[\u3400-\u9fff]+',text.lower())
    return [t for part in parts for t in (jieba.lcut(part) if re.search(r'[\u3400-\u9fff]',part) else [part]) if t.strip()]


class Retrieval:
    def __init__(self,embedding=None,cache_path='runtime/retrieval.sqlite',documents=None,history=None):
        self.embedding=embedding
        self.cache_path=Path(cache_path)
        self.documents=knowledge_documents() if documents is None else documents
        self.history=json.loads((DATA_DIR/'history.json').read_text(encoding='utf-8')) if history is None else history

    def _chunks(self,source_type,filters,omitted=None):
        docs=self.documents if source_type=='kb' else self.history
        result=[]
        for doc in docs:
            if source_type=='kb' and (not doc.get('reviewed') or doc.get('status')!='current' or doc.get('valid_from','')>WORLD_AS_OF[:10] or (doc.get('valid_until') and doc['valid_until']<WORLD_AS_OF[:10])):continue
            if filters.get('service_id') and doc.get('service_id')!=filters['service_id']:continue
            if filters.get('version'):
                versions=doc.get('applicable_versions',[doc.get('environment',{}).get('version')])
                if filters['version'] not in versions:continue
            if filters.get('region'):
                region=doc.get('region',doc.get('environment',{}).get('region'))
                if region and region not in (filters['region'],'global'):continue
            text=doc.get('text',json.dumps(doc,ensure_ascii=False))
            # Reviewed procedures remain whole; never cut a step off its caveat.
            # Oversized source sections are excluded explicitly rather than clipped.
            sections=re.split(r'\n(?=## )',text)
            for i,section in enumerate(sections):
                if len(section.encode('utf-8'))>12000:
                    if omitted is not None:omitted.append(f"{doc['id']}:{i}")
                    continue
                result.append({**{k:v for k,v in doc.items() if k!='path'},'text':section,'chunk_id':f"{doc['id']}:{i}",'source_type':source_type,'parent_id':doc['id']})
        return result

    def _vectors(self,chunks):
        signature={'model':MODEL,'dimension':DIMENSION,'index_version':INDEX_VERSION,'chunk_version':CHUNK_VERSION,
                   'complete_documents':[{k:v for k,v in doc.items() if k!='path'} for doc in self.documents],
                   'complete_history':self.history,'documents':chunks}
        key=hashlib.sha256(json.dumps(signature,sort_keys=True,ensure_ascii=False).encode()).hexdigest()
        manifest={'model':MODEL,'dimension':DIMENSION,'index_version':INDEX_VERSION,'chunk_version':CHUNK_VERSION,'content_hash':key}
        self.cache_path.parent.mkdir(parents=True,exist_ok=True)
        with closing(sqlite3.connect(self.cache_path)) as c:
            c.execute('CREATE TABLE IF NOT EXISTS vectors(cache_key TEXT PRIMARY KEY, payload TEXT NOT NULL)')
            row=c.execute('SELECT payload FROM vectors WHERE cache_key=?',(key,)).fetchone()
            if row:
                try:
                    cached=json.loads(row[0])
                    if not isinstance(cached,dict) or cached.get('manifest')!=manifest:raise ValueError('Index manifest mismatch')
                    vectors=np.asarray(cached['vectors'],dtype=float)
                    if vectors.shape==(len(chunks),DIMENSION) and np.isfinite(vectors).all() and (np.linalg.norm(vectors,axis=1)>0).all():return vectors
                except (ValueError,TypeError,KeyError):pass
            texts=[json.dumps({k:v for k,v in item.items() if k not in ('path',)},ensure_ascii=False) for item in chunks]
            vectors=self._validate(self.embedding.embed(texts),len(chunks))
            c.execute('INSERT OR REPLACE INTO vectors VALUES(?,?)',(key,json.dumps({'manifest':manifest,'vectors':vectors.tolist()})));c.commit()
            return vectors

    @staticmethod
    def _validate(vectors,count):
        value=np.asarray(vectors,dtype=float)
        if value.shape!=(count,DIMENSION) or not np.isfinite(value).all() or not (np.linalg.norm(value,axis=1)>0).all():raise ValueError('Embedding space mismatch')
        return value

    def search(self,query,filters=None,*,source_type='kb',top_k=5):
        if not isinstance(query,str) or not query.strip() or source_type not in ('kb','history') or type(top_k) is not int or not 1<=top_k<=10:raise ValueError('Invalid search')
        filters=filters or {}
        if not isinstance(filters,dict) or set(filters)-{'service_id','version','region'} or any(not isinstance(v,str) or not v for v in filters.values()):raise ValueError('Unknown search filter')
        omitted=[];chunks=self._chunks(source_type,filters,omitted)
        output={'hits':[],'degraded':self.embedding is None,'mode':'lexical','no_match':True,'degraded_reason':'embedding_disabled' if self.embedding is None else None,'index_version':INDEX_VERSION,
                'embedding_model':MODEL,'embedding_dimension':DIMENSION,'chunk_version':CHUNK_VERSION,'semantic_min_cosine':SEMANTIC_MIN_COSINE,'omitted_sections':omitted}
        if not chunks:return output
        tokens=[tokenize(' '.join([c.get('title',''),c['text'],c.get('service_id','')])) for c in chunks]
        index=bm25s.BM25();index.index(tokens,show_progress=False)
        ids,scores=index.retrieve([tokenize(query)],k=min(10,len(chunks)),show_progress=False)
        routes=[[int(i) for i,s in zip(ids[0],scores[0]) if s>0]]
        if self.embedding is not None:
            try:
                vectors=self._vectors(chunks)
                q=self._validate(self.embedding.embed([query]),1)[0]
                similarity=(vectors@q)/(np.linalg.norm(vectors,axis=1)*np.linalg.norm(q))
                routes.append([int(i) for i in np.argsort(-similarity)[:10] if similarity[i]>=SEMANTIC_MIN_COSINE])
                output.update(mode='hybrid',degraded=False)
            except Exception:
                output.update(degraded=True,degraded_reason='embedding_unavailable')
        fusion={}
        for route in routes:
            for rank,i in enumerate(route,1):fusion[i]=fusion.get(i,0)+1/(60+rank)
        parents=set()
        for i in sorted(fusion,key=lambda i:(-fusion[i],chunks[i]['chunk_id'])):
            item=chunks[i]
            if item['parent_id'] in parents:continue
            parents.add(item['parent_id']);output['hits'].append({**item,'rrf_score':fusion[i]})
            if len(output['hits'])>=min(5,top_k):break
        output['no_match']=not bool(output['hits'])
        return output

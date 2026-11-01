import copy
import pytest


class Vectors:
    def __init__(self): self.calls=[]
    def embed(self, texts):
        self.calls.append(texts)
        return [[1.0]+[0.0]*1023 for _ in texts]


def test_keyword_degradation_filters_and_no_match(tmp_path):
    from app.retrieval import Retrieval
    r=Retrieval(cache_path=tmp_path/'index.sqlite')
    hit=r.search('Okta ordinary lock', filters={'service_id':'okta','version':'2026.11'})
    assert hit['degraded'] and hit['mode']=='lexical'
    assert hit['hits'] and all(h['service_id']=='okta' for h in hit['hits'])
    assert r.search('Okta',{'service_id':'okta'})['hits']
    assert all(h['reviewed'] and h['status']=='current' for h in hit['hits'])
    assert r.search('zzunrelated9988123')['no_match']
    with pytest.raises(ValueError): r.search('okta',filters={'made_up':'yes'})


def test_cache_full_content_model_dimension_and_parent_dedup(tmp_path):
    from app.retrieval import Retrieval
    from app.seed import knowledge_documents
    docs=knowledge_documents()[:2]; vectors=Vectors(); path=tmp_path/'index.sqlite'
    r=Retrieval(embedding=vectors,cache_path=path,documents=docs)
    result=r.search('Okta'); assert not result['degraded'] and result['mode']=='hybrid'
    assert len({h['id'] for h in result['hits']})==len(result['hits'])
    assert len(vectors.calls)==2
    Retrieval(embedding=vectors,cache_path=path,documents=docs).search('Okta')
    assert len(vectors.calls)==3  # only query, documents reused
    changed=copy.deepcopy(docs);changed[0]['text']+='\nWhole changed content.'
    Retrieval(embedding=vectors,cache_path=path,documents=changed).search('Okta')
    assert len(vectors.calls)==5
    class Wrong:
        def embed(self,texts): return [[1,2] for _ in texts]
    assert Retrieval(embedding=Wrong(),cache_path=tmp_path/'wrong.sqlite',documents=docs).search('Okta')['degraded']


def test_failed_embedding_preserves_real_lexical_results(tmp_path):
    from app.retrieval import Retrieval
    class Broken:
        def embed(self,texts): raise TimeoutError()
    result=Retrieval(embedding=Broken(),cache_path=tmp_path/'cache.sqlite').search('VPN 5.2 reconnect')
    assert result['hits'] and result['degraded'] and result['degraded_reason']=='embedding_unavailable'
    history=Retrieval(cache_path=tmp_path/'cache.sqlite').search('ordinary lock',source_type='history')
    assert all(h['source_type']=='history' and h['id'].startswith('history.') for h in history['hits'])


def test_unrelated_positive_vectors_can_still_be_no_match(tmp_path):
    from app.retrieval import Retrieval
    from app.seed import knowledge_documents
    class LowSimilarity:
        def embed(self,texts):
            return [[.4,.916515]+[0.0]*1022] if texts==['zzunrelated9988123'] else [[1.0]+[0.0]*1023 for _ in texts]
    result=Retrieval(embedding=LowSimilarity(),cache_path=tmp_path/'cache.sqlite',documents=knowledge_documents()[:2]).search('zzunrelated9988123')
    assert not result['degraded'] and result['mode']=='hybrid' and result['no_match'] and not result['hits']


def test_oversized_reviewed_section_is_explicitly_reported_never_cut(tmp_path):
    from app.retrieval import Retrieval
    from app.seed import knowledge_documents
    doc=copy.deepcopy(knowledge_documents()[0]);doc['text']='Important prerequisite '+('whole step '*2000)
    result=Retrieval(cache_path=tmp_path/'cache.sqlite',documents=[doc]).search('Important prerequisite')
    assert result['no_match'] and result['omitted_sections']==[doc['id']+':0']

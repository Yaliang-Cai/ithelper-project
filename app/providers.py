"""Bounded synchronous provider adapters. No retries and no model fallback."""
import json
import math
import httpx

class ProviderError(RuntimeError):
    pass

class EmbeddingDisabled(ProviderError):
    pass

def _request(settings, base_url, key, path, payload=None, transport=None):
    failed = False
    try:
        with httpx.Client(base_url=base_url.rstrip('/')+'/',headers={'Authorization':'Bearer '+key},timeout=httpx.Timeout(45,connect=10),transport=transport,follow_redirects=False) as client:
            response = client.get(path) if payload is None else client.post(path,json=payload)
            if response.status_code != 200:
                failed = True
                result = None
            else:
                result = response.json()
    except (httpx.HTTPError, ValueError, TypeError):
        failed = True
        result = None
    if failed:
        raise ProviderError('Provider request failed; request details withheld')
    if not isinstance(result,dict):
        raise ProviderError('Invalid provider response')
    return result

class DeepSeekClient:
    def __init__(self, settings, budget, transport=None):
        self.settings, self.budget, self.transport = settings, budget, transport

    def list_models(self):
        if not self.settings.deepseek_api_key:
            raise ProviderError('DeepSeek key is missing')
        data = _request(self.settings,self.settings.deepseek_base_url,self.settings.deepseek_api_key,'models',transport=self.transport)
        if not isinstance(data.get('data'),list):
            raise ProviderError('Invalid model list')
        return [m.get('id') for m in data['data'] if isinstance(m,dict)]

    def complete(self, messages, *, purpose, tools=None, response_format=None, thinking=False, max_tokens=2048, tool_choice=None):
        if not self.settings.deepseek_api_key:
            raise ProviderError('DeepSeek key is missing')
        if not isinstance(max_tokens,int) or isinstance(max_tokens,bool) or not 1 <= max_tokens <= 8192:
            raise ValueError('Invalid output token limit')
        payload = {'model':self.settings.deepseek_model,'messages':messages,'thinking':{'type':'enabled' if thinking else 'disabled'},'max_tokens':max_tokens,'stream':False}
        for name,value in [('tools',tools),('response_format',response_format),('tool_choice',tool_choice)]:
            if value is not None:
                payload[name] = value
        # UTF-8 bytes are a conservative upper bound on tokenizer input, with
        # generous protocol overhead per message/tool; output includes reasoning.
        input_bound = len(json.dumps(payload,ensure_ascii=False).encode('utf-8')) + 1024 + 128 * (len(messages) + len(tools or []))
        reservation = self.budget.reserve((input_bound*.30+max_tokens*1.20)/1e6,purpose=purpose)
        data = _request(self.settings,self.settings.deepseek_base_url,self.settings.deepseek_api_key,'chat/completions',payload,self.transport)
        valid = False
        try:
            message = data['choices'][0]['message']
            valid = isinstance(message,dict) and message.get('role') == 'assistant' and (isinstance(message.get('content'),str) or bool(message.get('tool_calls')))
            if 'model' in data and data['model'] != self.settings.deepseek_model:
                valid = False
            for call in message.get('tool_calls',[]):
                valid = valid and isinstance(call['id'],str) and call['type']=='function' and isinstance(call['function']['name'],str) and isinstance(call['function']['arguments'],str)
        except (KeyError,IndexError,TypeError):
            valid = False
        if not valid:
            raise ProviderError('Invalid completion response')
        usage = data.get('usage')
        if isinstance(usage,dict):
            counts = [usage.get('prompt_tokens'),usage.get('completion_tokens')]
            if all(isinstance(x,int) and not isinstance(x,bool) and x>=0 for x in counts):
                self.budget.settle(reservation,(counts[0]*.30+counts[1]*1.20)/1e6)
        return data

class EmbeddingClient:
    def __init__(self, settings, transport=None):
        self.settings, self.transport = settings, transport

    def embed(self, texts):
        if not self.settings.siliconflow_api_key:
            raise EmbeddingDisabled('Embedding key missing; lexical retrieval only')
        if not texts or any(not isinstance(t,str) or not t.strip() for t in texts):
            raise ValueError('Embedding input must contain nonempty text')
        data = _request(self.settings,self.settings.embedding_base_url,self.settings.siliconflow_api_key,'embeddings',{'model':self.settings.embedding_model,'input':texts,'encoding_format':'float'},self.transport)
        valid = False
        try:
            rows = sorted(data['data'],key=lambda row:row['index'])
            vectors = [row['embedding'] for row in rows]
            valid = len(rows)==len(texts) and [r['index'] for r in rows]==list(range(len(texts))) and all(isinstance(v,list) and len(v)>0 and len(v)==len(vectors[0]) and all(isinstance(n,(int,float)) and not isinstance(n,bool) and math.isfinite(n) for n in v) for v in vectors)
        except (KeyError,TypeError,ValueError):
            valid = False
        if not valid:
            raise ProviderError('Invalid embedding response')
        return [[float(n) for n in v] for v in vectors]

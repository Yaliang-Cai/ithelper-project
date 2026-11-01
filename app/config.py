"""Validated provider configuration; secrets never appear in repr."""
from dataclasses import dataclass, field, fields
from pathlib import Path
import math
import os
from dotenv import dotenv_values

@dataclass(frozen=True)
class Settings:
    deepseek_api_key: str = field(default='', repr=False)
    siliconflow_api_key: str = field(default='', repr=False)
    deepseek_model: str = 'deepseek-flash'
    deepseek_base_url: str = 'https://api.deepseek.com'
    embedding_model: str = 'BAAI/bge-m3'
    embedding_base_url: str = 'https://api.siliconflow.cn/v1'
    llm_budget_usd: float = 2.0
    runtime_dir: Path = Path('runtime')

    def __post_init__(self):
        if self.deepseek_model != 'deepseek-flash' or self.embedding_model != 'BAAI/bge-m3':
            raise ValueError('Only the authorized model IDs are allowed')
        if self.deepseek_base_url.rstrip('/') not in ('https://api.deepseek.com','https://api.deepseek.com/v1') or self.embedding_base_url.rstrip('/') != 'https://api.siliconflow.cn/v1':
            raise ValueError('Only official provider endpoints are allowed')
        if not math.isfinite(self.llm_budget_usd) or self.llm_budget_usd <= 0:
            raise ValueError('Budget must be positive and finite')
        object.__setattr__(self, 'runtime_dir', Path(self.runtime_dir))

    @classmethod
    def load(cls, env_file='.env'):
        values = {**dotenv_values(env_file), **os.environ}
        kwargs = {f.name:values[f.name.upper()] for f in fields(cls) if values.get(f.name.upper()) not in (None,'')}
        if 'llm_budget_usd' in kwargs:
            try:
                kwargs['llm_budget_usd'] = float(kwargs['llm_budget_usd'])
            except ValueError:
                raise ValueError('Invalid budget configuration') from None
        return cls(**kwargs)


"""Learned lexical CPU baseline, not a factuality judge or a language model."""
from __future__ import annotations

import hashlib
import json
import math
import re
import unicodedata
from collections import Counter
from dataclasses import dataclass
from pathlib import Path

import numpy as np

DIMENSION = 1024
FEATURE_VERSION = 'lexical_overlap_blake2s_v1'
MODEL_TYPE = 'cpu_pairwise_linear_v1'


def _tokens(text):
    return re.findall(r'\w+', unicodedata.normalize('NFKC', text).casefold())


def features(prompt, answer):
    """Hashed answer unigrams/bigrams + prompt-word overlap; bounded Unicode input."""
    words = _tokens(answer)
    context = set(_tokens(' '.join(m['content'] for m in prompt)))
    counts = Counter('word:' + w for w in words)
    counts.update('bigram:' + a + ':' + b for a, b in zip(words, words[1:]))
    counts.update('overlap:' + w for w in words if w in context)
    vector = np.zeros(DIMENSION, dtype=np.float64)
    for term, count in counts.items():
        digest = hashlib.blake2s(term.encode('utf-8'), digest_size=8).digest()
        index = int.from_bytes(digest[:4], 'little') % DIMENSION
        vector[index] += (1 if digest[4] & 1 else -1) * (1 + math.log(count))
    norm = np.linalg.norm(vector)
    return vector / norm if norm else vector


def _identity(payload):
    core = {k: v for k, v in payload.items() if k != 'model_id'}
    return hashlib.sha256(json.dumps(core, sort_keys=True, ensure_ascii=False, allow_nan=False,
                                     separators=(',', ':')).encode()).hexdigest()


@dataclass
class RewardModel:
    weights: np.ndarray
    metadata: dict

    @property
    def model_id(self):
        return _identity(self.payload())

    def score(self, prompt, answer):
        return float(features(prompt, answer) @ self.weights)

    def payload(self):
        return {'schema_version': 1, 'model_type': MODEL_TYPE, 'feature_version': FEATURE_VERSION,
                'dimension': DIMENSION, 'weights': self.weights.tolist(), 'metadata': self.metadata}

    def save(self, path):
        payload = self.payload()
        payload['model_id'] = _identity(payload)
        # Caller creates a private staging directory and promotes it atomically.
        with Path(path).open('x', encoding='utf-8') as stream:
            json.dump(payload, stream, ensure_ascii=False, sort_keys=True, allow_nan=False)

    @classmethod
    def load(cls, path):
        path = Path(path)
        if path.stat().st_size > 1024 * 1024:
            raise ValueError('reward artifact exceeds 1 MiB')
        try:
            payload = json.loads(path.read_text(encoding='utf-8'))
            weights = np.asarray(payload['weights'], dtype=np.float64)
            metadata = payload['metadata']
            if (payload['schema_version'] != 1 or payload['model_type'] != MODEL_TYPE or
                    payload['feature_version'] != FEATURE_VERSION or payload['dimension'] != DIMENSION or
                    weights.shape != (DIMENSION,) or not np.isfinite(weights).all() or
                    not isinstance(metadata, dict) or metadata.get('purpose') not in {'demo', 'production'} or
                    payload['model_id'] != _identity(payload)):
                raise ValueError('invalid reward artifact')
        except (KeyError, TypeError, json.JSONDecodeError, OverflowError) as exc:
            raise ValueError('invalid reward artifact') from exc
        return cls(weights, metadata)


def fit(records, *, purpose, epochs=300, learning_rate=0.5, l2=0.001):
    if purpose not in {'demo', 'production'}:
        raise ValueError('purpose must be demo or production')
    if not records or not 1 <= epochs <= 5000 or not 0 < learning_rate <= 2 or not 0 < l2 <= 1:
        raise ValueError('invalid reward training inputs or hyperparameters')
    differences = np.stack([features(r['prompt'], r['chosen']) - features(r['prompt'], r['rejected'])
                            for r in records])
    if not np.any(differences):
        raise ValueError('features cannot distinguish these preferences')
    weights = np.zeros(DIMENSION, dtype=np.float64)
    for _ in range(epochs):
        margins = differences @ weights
        error = np.exp(-np.logaddexp(0, margins))
        weights -= learning_rate * (-differences.T @ error / len(records) + l2 * weights)
    return RewardModel(weights, {'purpose': purpose, 'training_records': len(records),
                                'epochs': epochs, 'learning_rate': learning_rate, 'l2': l2})


def evaluate(model, records):
    if not records:
        raise ValueError('evaluation requires held-out preferences')
    margins = np.asarray([model.score(r['prompt'], r['chosen']) - model.score(r['prompt'], r['rejected'])
                          for r in records])
    wins = int((margins > 1e-8).sum())
    n = len(records)
    p = wins / n
    z = 1.96
    lower = (p + z*z/(2*n) - z * math.sqrt(p*(1-p)/n + z*z/(4*n*n))) / (1 + z*z/n)
    return {'records': n, 'correct': wins, 'ties': int((np.abs(margins) <= 1e-8).sum()),
            'accuracy': p, 'accuracy_wilson_lower_95': lower,
            'log_loss': float(np.logaddexp(0, -margins).mean())}

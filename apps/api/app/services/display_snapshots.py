"""Persisted hourly display snapshots. Source workbooks remain authoritative.

Readers never wait for a background rebuild when a saved snapshot exists.
Manual refresh rebuilds from source and atomically replaces the saved snapshot.
"""
from __future__ import annotations

import gzip
import json
import logging
import os
import threading
from datetime import datetime
from pathlib import Path
from uuid import uuid4

from fastapi import HTTPException, Request, Response
from fastapi.encoders import jsonable_encoder
from app.services.blind_workspace import ROOT, TZ

logger = logging.getLogger('bidos.display_snapshots')


class DisplaySnapshots:
    def __init__(self, builders, invalidate, directory=None):
        self.builders = builders
        self.invalidate = invalidate
        self.directory = Path(directory or ROOT / 'data/display_snapshots')
        self._entries = {}
        self._state_lock = threading.Lock()
        self._build_lock = threading.Lock()
        self._building = False
        self._last_error = ''
        self._last_attempt = ''
        for kind in builders:
            path = self.directory / (kind + '.json.gz')
            if path.exists():
                try:
                    compressed = path.read_bytes()
                    raw = gzip.decompress(compressed)
                    metadata = json.loads(raw)['snapshot']
                    self._entries[kind] = (raw, compressed, metadata['generatedAt'])
                except (OSError, ValueError, KeyError):
                    logger.warning('显示快照 %s 无法读取，等待重新生成', kind)

    def status(self):
        with self._state_lock:
            return {'intervalMinutes': 60, 'refreshing': self._building,
                    'lastAttempt': self._last_attempt, 'lastError': self._last_error,
                    'snapshots': {kind: {'generatedAt': entry[2], 'bytes': len(entry[0])}
                                  for kind, entry in self._entries.items()},
                    'directory': str(self.directory)}

    def refresh(self, kind=None):
        kinds = [kind] if kind else list(self.builders)
        with self._build_lock:
            with self._state_lock:
                self._building = True
                self._last_attempt = datetime.now(TZ).isoformat()
            try:
                self.invalidate()
                self.directory.mkdir(parents=True, exist_ok=True)
                updated = {}
                for name in kinds:
                    data = self.builders[name]()
                    at = datetime.now(TZ).isoformat()
                    data['snapshot'] = {'generatedAt': at, 'intervalMinutes': 60}
                    raw = json.dumps(jsonable_encoder(data), ensure_ascii=False, separators=(',', ':')).encode()
                    compressed = gzip.compress(raw, compresslevel=3)
                    target = self.directory / (name + '.json.gz')
                    temp = target.with_name(target.name + '.' + uuid4().hex + '.tmp')
                    try:
                        with temp.open('wb') as handle:
                            handle.write(compressed)
                            handle.flush()
                            os.fsync(handle.fileno())
                        os.replace(temp, target)
                    finally:
                        if temp.exists(): temp.unlink()
                    with self._state_lock:
                        self._entries[name] = (raw, compressed, at)
                    updated[name] = at
                with self._state_lock:
                    self._last_error = ''
                return {'status': '完成', 'updated': updated}
            except Exception as exc:
                with self._state_lock:
                    self._last_error = f'快照更新失败（{type(exc).__name__}），保留上次成功数据'
                logger.exception('显示快照更新失败')
                raise RuntimeError(self._last_error) from exc
            finally:
                with self._state_lock:
                    self._building = False

    def response(self, kind, request: Request, force=False):
        with self._state_lock:
            present = kind in self._entries
        if force or not present:
            try:
                self.refresh(kind)
            except RuntimeError as exc:
                raise HTTPException(status_code=503, detail=str(exc)) from exc
        with self._state_lock:
            raw, compressed, at = self._entries[kind]
        encoded = 'gzip' in request.headers.get('accept-encoding', '').lower()
        headers = {'Cache-Control': 'no-store', 'Vary': 'Accept-Encoding', 'X-BidOS-Snapshot-At': at}
        if encoded: headers['Content-Encoding'] = 'gzip'
        return Response(compressed if encoded else raw, media_type='application/json', headers=headers)

    def warm(self):
        """Reuse saved data on restart; rebuild missing or older than one hour."""
        now = datetime.now(TZ)
        with self._state_lock:
            old = len(self._entries) != len(self.builders) or any(
                (now - datetime.fromisoformat(entry[2])).total_seconds() >= 3600
                for entry in self._entries.values())
        if old:
            self.refresh()

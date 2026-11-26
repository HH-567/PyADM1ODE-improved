"""工作区：把一次长期运行固化成一个可以随时打开、接着跑的空间。

一个工作区 = 一个目录，里面放着

* ``meta.json``   名称、创建时间、说明、当前进度摘要
* ``daily.json``  按天推进的状态（装置状态、天数、曲线、加料历史）
* ``sensors.json`` 传感器会话的配置与最近一次快照

为什么要存装置状态而不是只存输入：ADM1 是 41 维动力学系统，"第几天"不足以
复现现场——必须把当时的内部状态一起存下来，否则重新打开等于换了一罐料。

当前活动工作区记录在 ``workspaces/active.txt``，重启服务后自动恢复。
"""

from __future__ import annotations

import json
import re
import time
from pathlib import Path
from typing import Any

import numpy as np

ROOT = Path(__file__).with_name("workspaces")
ACTIVE = ROOT / "active.txt"

META = "meta.json"
DAILY = "daily.json"
SENSORS = "sensors.json"


def _jsonable(obj: Any) -> Any:
    """Make NumPy / dataclass debris safe for ``json.dump``."""
    if isinstance(obj, dict):
        return {str(k): _jsonable(v) for k, v in obj.items()}
    if isinstance(obj, np.ndarray):
        return [_jsonable(v) for v in obj.tolist()]
    if isinstance(obj, (list, tuple)):
        return [_jsonable(v) for v in obj]
    if isinstance(obj, (np.floating, np.integer)):
        v = obj.item()
        return v if np.isfinite(v) else None
    if isinstance(obj, float):
        return obj if np.isfinite(obj) else None
    if isinstance(obj, (str, int, bool)) or obj is None:
        return obj
    return str(obj)


def _slug(name: str) -> str:
    out = "".join(ch if ch.isalnum() else "_" for ch in name).strip("_").lower()
    return re.sub(r"_+", "_", out) or "workspace"


class WorkspaceManager:
    """Create / open / persist workspaces on disk."""

    def __init__(self, root: Path | None = None) -> None:
        self.root = root or ROOT
        self.root.mkdir(parents=True, exist_ok=True)
        self.active_id: str | None = None
        if ACTIVE.exists():
            try:
                self.active_id = ACTIVE.read_text(encoding="utf-8").strip() or None
            except Exception:  # noqa: BLE001
                self.active_id = None
        if self.active_id and not (self.root / self.active_id).is_dir():
            self.active_id = None

    # -- paths -----------------------------------------------------------
    def _dir(self, ws_id: str) -> Path:
        safe = _slug(ws_id)
        return self.root / safe

    def _read(self, path: Path) -> dict[str, Any]:
        if not path.exists():
            return {}
        try:
            return json.loads(path.read_text(encoding="utf-8"))
        except Exception:  # noqa: BLE001 - 坏文件不该让页面打不开
            return {}

    def _write(self, path: Path, payload: dict[str, Any]) -> None:
        path.parent.mkdir(parents=True, exist_ok=True)
        tmp = path.with_suffix(path.suffix + ".tmp")
        tmp.write_text(json.dumps(_jsonable(payload), ensure_ascii=False, indent=1), encoding="utf-8")
        tmp.replace(path)

    # -- CRUD ------------------------------------------------------------
    def list(self) -> list[dict[str, Any]]:
        items = []
        for d in sorted(self.root.iterdir()):
            if not d.is_dir():
                continue
            meta = self._read(d / META)
            if not meta:
                continue
            items.append(
                {
                    "id": d.name,
                    "name": meta.get("name", d.name),
                    "note": meta.get("note", ""),
                    "created": meta.get("created"),
                    "updated": meta.get("updated"),
                    "summary": meta.get("summary", {}),
                    "active": d.name == self.active_id,
                }
            )
        items.sort(key=lambda x: x.get("updated") or 0, reverse=True)
        return items

    def create(self, name: str, note: str = "") -> dict[str, Any]:
        base = _slug(name)
        ws_id = base
        i = 2
        while self._dir(ws_id).exists():
            ws_id = f"{base}_{i}"
            i += 1
        now = time.time()
        self._write(
            self._dir(ws_id) / META,
            {"name": name, "note": note, "created": now, "updated": now, "summary": {}},
        )
        return {"id": ws_id, "name": name, "note": note, "created": now, "updated": now}

    def open(self, ws_id: str) -> dict[str, Any]:
        d = self._dir(ws_id)
        if not d.is_dir():
            raise ValueError(f"工作区 {ws_id} 不存在")
        self.active_id = d.name
        ACTIVE.write_text(d.name, encoding="utf-8")
        return {"id": d.name, **self._read(d / META)}

    def rename(self, ws_id: str, name: str) -> dict[str, Any]:
        d = self._dir(ws_id)
        if not d.is_dir():
            raise ValueError(f"工作区 {ws_id} 不存在")
        meta = self._read(d / META)
        meta["name"] = name
        meta["updated"] = time.time()
        self._write(d / META, meta)
        return {"id": d.name, **meta}

    def delete(self, ws_id: str) -> bool:
        d = self._dir(ws_id)
        if not d.is_dir():
            return False
        for f in d.glob("*"):
            f.unlink(missing_ok=True)
        d.rmdir()
        if self.active_id == d.name:
            self.active_id = None
            ACTIVE.unlink(missing_ok=True)
        return True

    # -- payloads --------------------------------------------------------
    def save(self, kind: str, payload: dict[str, Any], summary: dict[str, Any] | None = None) -> str | None:
        """Persist a session payload into the active workspace."""
        if not self.active_id:
            return None
        d = self._dir(self.active_id)
        self._write(d / f"{kind}.json", payload)
        meta = self._read(d / META)
        meta["updated"] = time.time()
        if summary:
            meta["summary"] = {**(meta.get("summary") or {}), **summary}
        self._write(d / META, meta)
        return self.active_id

    def load(self, kind: str) -> dict[str, Any]:
        if not self.active_id:
            return {}
        return self._read(self._dir(self.active_id) / f"{kind}.json")

    def info(self) -> dict[str, Any]:
        if not self.active_id:
            return {"id": None, "name": None}
        meta = self._read(self._dir(self.active_id) / META)
        return {"id": self.active_id, **meta}

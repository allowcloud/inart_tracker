from __future__ import annotations

import datetime
import json
import os
import uuid
from pathlib import Path

from core.constants import (
    DEFAULT_DB,
    DEFAULT_PROJECT_FIELDS,
    DEFAULT_SYS_CFG,
    PRINT_TRACK_LOCATION_DEFAULTS,
    STANDARD_EVENTS_KEY,
    SYSTEM_CONFIG_KEY,
    TODO_LIST_KEY,
    deep_copy_obj,
)
from core.shared_logic import compute_stale_doc_keys


IMG_DIR_NAME = "img_assets"


class LocalJsonStorageManager:
    backend_name = "Local JSON"
    attachment_mode = "local-file"

    def __init__(self, path="tracker_data_web_v20.json", attachment_dir=None):
        self.path = Path(path)
        self.attachment_dir = Path(attachment_dir) if attachment_dir else self.path.parent / IMG_DIR_NAME

    def load(self):
        if self.path.exists():
            for encoding in ("utf-8", "utf-8-sig", "gbk"):
                try:
                    data = json.loads(self.path.read_text(encoding=encoding))
                    if isinstance(data, dict):
                        return data
                except Exception:
                    continue
        return {SYSTEM_CONFIG_KEY: deep_copy_obj(DEFAULT_SYS_CFG)}

    def save(self, data):
        self.path.parent.mkdir(parents=True, exist_ok=True)
        self.path.write_text(json.dumps(data, ensure_ascii=False, indent=2), encoding="utf-8")

    def save_one(self, key, value):
        data = self.load()
        data[key] = value
        self.save(data)

    def delete_one(self, key):
        data = self.load()
        data.pop(key, None)
        self.save(data)

    def save_file_bytes(self, file_bytes, filename="", prefix="upload"):
        self.attachment_dir.mkdir(parents=True, exist_ok=True)
        ext = Path(filename or "").suffix.lower() or ".jpg"
        file_name = f"{prefix}_{uuid.uuid4().hex}{ext}"
        file_path = self.attachment_dir / file_name
        file_path.write_bytes(bytes(file_bytes or b""))
        return f"FILE:{file_name}"

    def read_file_bytes(self, ref):
        if not isinstance(ref, str) or not ref.startswith("FILE:"):
            return None
        file_path = self.attachment_dir / ref.replace("FILE:", "", 1)
        if not file_path.exists():
            return None
        return file_path.read_bytes()

    def import_file_bytes(self, ref, file_bytes, filename=""):
        self.attachment_dir.mkdir(parents=True, exist_ok=True)
        if isinstance(ref, str) and ref.startswith("FILE:"):
            file_name = ref.replace("FILE:", "", 1)
        else:
            ext = Path(filename or "").suffix.lower() or ".jpg"
            file_name = f"restore_{uuid.uuid4().hex}{ext}"
        file_path = self.attachment_dir / file_name
        file_path.write_bytes(bytes(file_bytes or b""))
        return f"FILE:{file_name}"


class MemoryStorageManager:
    backend_name = "Memory"
    attachment_mode = "memory-file"

    def __init__(self):
        self._data = deep_copy_obj(DEFAULT_DB)
        self._files = {}

    def load(self):
        return deep_copy_obj(self._data)

    def save(self, data):
        self._data = deep_copy_obj(data)

    def save_one(self, key, value):
        self._data[key] = deep_copy_obj(value)

    def delete_one(self, key):
        self._data.pop(key, None)

    def save_file_bytes(self, file_bytes, filename="", prefix="upload"):
        safe_name = filename or f"{prefix}.jpg"
        ext = Path(safe_name).suffix.lower() or ".jpg"
        file_id = f"MEMORY:{prefix}_{uuid.uuid4().hex}{ext}"
        self._files[file_id] = bytes(file_bytes or b"")
        return file_id

    def read_file_bytes(self, ref):
        if not isinstance(ref, str) or not ref.startswith("MEMORY:"):
            return None
        return self._files.get(ref)

    def import_file_bytes(self, ref, file_bytes, filename=""):
        if isinstance(ref, str) and ref.startswith("MEMORY:"):
            self._files[ref] = bytes(file_bytes or b"")
            return ref
        return self.save_file_bytes(file_bytes, filename=filename, prefix="restore")


class MongoStorageManager:
    backend_name = "MongoDB"
    attachment_mode = "gridfs"

    def __init__(self, uri):
        self.client = None
        self.db = None
        self.col = None
        self.fs = None
        self.PyMongoError = Exception
        self.NoFile = FileNotFoundError
        self.ObjectId = lambda raw: raw
        self.init_error = ""

        try:
            from bson import ObjectId
            from gridfs import GridFS, NoFile
            from pymongo import MongoClient
            from pymongo.errors import PyMongoError
        except Exception as exc:
            self.init_error = f"Mongo 依赖初始化失败：{exc}"
            return

        self.PyMongoError = PyMongoError
        self.NoFile = NoFile
        self.ObjectId = ObjectId
        if not uri:
            self.init_error = "未检测到 MONGO_URI。"
            return

        try:
            self.client = MongoClient(
                uri,
                serverSelectionTimeoutMS=10000,
                connectTimeoutMS=10000,
                socketTimeoutMS=10000,
                maxPoolSize=5,
            )
            self.db = self.client["inart_pm"]
            self.col = self.db["projects"]
            self.fs = GridFS(self.db, collection="attachments")
        except Exception as exc:
            self.client = None
            self.db = None
            self.col = None
            self.fs = None
            self.init_error = f"Mongo 连接失败：{exc}"

    @property
    def is_ready(self):
        return self.col is not None and self.fs is not None

    def _ensure_ready(self):
        if not self.is_ready:
            raise RuntimeError(self.init_error or "MongoDB 当前不可用。")

    def load(self):
        self._ensure_ready()
        try:
            docs = list(self.col.find({}, {"_id": 0}))
        except Exception as exc:
            raise RuntimeError(f"Mongo 读取失败：{exc}") from exc

        data = {}
        for doc in docs:
            key = doc.get("_doc_key")
            if key:
                data[key] = doc.get("payload", {})
        return data if data else deep_copy_obj(DEFAULT_DB)

    def save(self, data):
        self._ensure_ready()
        try:
            from pymongo import UpdateOne

            target_data = data if isinstance(data, dict) else {}
            target_keys = [str(key).strip() for key in target_data.keys() if str(key).strip()]
            existing_keys = {
                str(doc.get("_doc_key", "")).strip()
                for doc in self.col.find({}, {"_doc_key": 1})
                if str(doc.get("_doc_key", "")).strip()
            }
            ops = [
                UpdateOne({"_doc_key": key}, {"$set": {"_doc_key": key, "payload": value}}, upsert=True)
                for key, value in target_data.items()
            ]
            if ops:
                self.col.bulk_write(ops, ordered=False)
            stale_keys = compute_stale_doc_keys(existing_keys, target_keys)
            if stale_keys:
                self.col.delete_many({"_doc_key": {"$in": stale_keys}})
        except self.PyMongoError as exc:
            raise RuntimeError(f"Mongo 保存失败：{exc}") from exc

    def save_one(self, key, value):
        self._ensure_ready()
        try:
            self.col.replace_one({"_doc_key": key}, {"_doc_key": key, "payload": value}, upsert=True)
        except self.PyMongoError as exc:
            raise RuntimeError(f"Mongo 保存失败 [{key}]：{exc}") from exc

    def delete_one(self, key):
        self._ensure_ready()
        try:
            self.col.delete_one({"_doc_key": key})
        except self.PyMongoError as exc:
            raise RuntimeError(f"Mongo 删除失败 [{key}]：{exc}") from exc

    def save_file_bytes(self, file_bytes, filename="", prefix="upload"):
        self._ensure_ready()
        try:
            safe_name = filename or f"{prefix}_{uuid.uuid4().hex}.jpg"
            file_id = self.fs.put(
                file_bytes,
                filename=safe_name,
                contentType="image/jpeg",
                createdAt=datetime.datetime.utcnow(),
            )
            return f"GRIDFS:{file_id}"
        except self.PyMongoError as exc:
            raise RuntimeError(f"Mongo 附件保存失败：{exc}") from exc

    def read_file_bytes(self, ref):
        self._ensure_ready()
        if not isinstance(ref, str) or not ref.startswith("GRIDFS:"):
            return None
        raw_id = ref.replace("GRIDFS:", "", 1)
        try:
            file_id = self.ObjectId(raw_id)
        except Exception:
            file_id = raw_id
        try:
            return self.fs.get(file_id).read()
        except self.NoFile:
            return None
        except self.PyMongoError as exc:
            raise RuntimeError(f"Mongo 附件读取失败：{exc}") from exc

    def import_file_bytes(self, ref, file_bytes, filename=""):
        self._ensure_ready()
        if not isinstance(ref, str) or not ref.startswith("GRIDFS:"):
            return self.save_file_bytes(file_bytes, filename=filename, prefix="restore")
        raw_id = ref.replace("GRIDFS:", "", 1)
        try:
            file_id = self.ObjectId(raw_id)
        except Exception:
            return self.save_file_bytes(file_bytes, filename=filename, prefix="restore")
        try:
            if not self.fs.exists(file_id):
                self.fs.put(
                    file_bytes,
                    _id=file_id,
                    filename=filename or f"{raw_id}.jpg",
                    contentType="image/jpeg",
                    createdAt=datetime.datetime.utcnow(),
                )
            return ref
        except self.PyMongoError:
            return self.save_file_bytes(file_bytes, filename=filename, prefix="restore")


def get_mongo_uri():
    return os.environ.get("MONGO_URI", "")


def build_storage_manager(force_local=False, json_path="tracker_data_web_v20.json", mongo_uri=None, prefer_local=True):
    if os.environ.get("INART_ALLOW_MEMORY_DB", "").strip() == "1":
        return MemoryStorageManager()

    data_path = Path(json_path)
    backend_pref = os.environ.get("INART_STORAGE_BACKEND", "").strip().lower()
    resolved_mongo_uri = mongo_uri or get_mongo_uri()

    if force_local or backend_pref in {"json", "local"}:
        return LocalJsonStorageManager(path=data_path)

    if backend_pref == "mongo":
        mongo_manager = MongoStorageManager(resolved_mongo_uri)
        if mongo_manager.is_ready or not prefer_local:
            return mongo_manager
        return LocalJsonStorageManager(path=data_path)

    if prefer_local and data_path.exists():
        return LocalJsonStorageManager(path=data_path)

    if resolved_mongo_uri:
        mongo_manager = MongoStorageManager(resolved_mongo_uri)
        if mongo_manager.is_ready:
            return mongo_manager
        if prefer_local:
            return LocalJsonStorageManager(path=data_path)
        return mongo_manager

    return LocalJsonStorageManager(path=data_path)


def ensure_db_shape(db_obj):
    if not isinstance(db_obj, dict):
        db_obj = {}

    cfg = db_obj.get(SYSTEM_CONFIG_KEY)
    if not isinstance(cfg, dict):
        db_obj[SYSTEM_CONFIG_KEY] = {}
        cfg = db_obj[SYSTEM_CONFIG_KEY]

    for key, value in DEFAULT_SYS_CFG.items():
        if key not in cfg:
            cfg[key] = deep_copy_obj(value)

    if not isinstance(cfg.get("打印追踪列表"), list):
        cfg["打印追踪列表"] = []
    if not isinstance(cfg.get("打印地点选项"), list):
        cfg["打印地点选项"] = PRINT_TRACK_LOCATION_DEFAULTS.copy()
    if not isinstance(cfg.get(TODO_LIST_KEY), list):
        cfg[TODO_LIST_KEY] = []
    if not isinstance(cfg.get(STANDARD_EVENTS_KEY), list):
        cfg[STANDARD_EVENTS_KEY] = []

    for project_name, project_data in list(db_obj.items()):
        if project_name == SYSTEM_CONFIG_KEY:
            continue
        if not isinstance(project_data, dict):
            db_obj[project_name] = {}
            project_data = db_obj[project_name]

        for key, default_value in DEFAULT_PROJECT_FIELDS.items():
            if key not in project_data:
                project_data[key] = deep_copy_obj(default_value)

        if not isinstance(project_data.get("计划排期"), list):
            project_data["计划排期"] = []
        if not isinstance(project_data.get("周会备注"), list):
            project_data["周会备注"] = []
        if not isinstance(project_data.get("部件列表"), dict):
            project_data["部件列表"] = {}
        if not isinstance(project_data.get("发货数据"), dict):
            project_data["发货数据"] = {}
        if not isinstance(project_data.get("成本数据"), dict):
            project_data["成本数据"] = {}
        if not isinstance(project_data.get("print_tracking"), list):
            project_data["print_tracking"] = []
        if not isinstance(project_data.get("garment_flow"), dict):
            project_data["garment_flow"] = {}
        if not isinstance(project_data.get("包装专项"), dict):
            project_data["包装专项"] = {}
        if not isinstance(project_data.get("备忘录"), str):
            project_data["备忘录"] = str(project_data.get("备忘录", "") or "")

    return db_obj

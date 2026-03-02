from fastapi import FastAPI, HTTPException
from fastapi.middleware.cors import CORSMiddleware
from pymongo import MongoClient
from pymongo.errors import PyMongoError
from typing import Any
from pydantic import BaseModel
import os
from dotenv import load_dotenv

load_dotenv()

app = FastAPI(title="INART PM API")

app.add_middleware(
    CORSMiddleware,
    allow_origins=["*"],
    allow_credentials=True,
    allow_methods=["*"],
    allow_headers=["*"],
)

MONGO_URI = os.environ.get("MONGO_URI", "")
if not MONGO_URI:
    raise RuntimeError("请在 .env 文件中配置 MONGO_URI")

client = MongoClient(MONGO_URI, serverSelectionTimeoutMS=10000, connectTimeoutMS=10000, socketTimeoutMS=10000)
col = client["inart_pm"]["projects"]

def load_all():
    try:
        docs = list(col.find({}, {"_id": 0}))
        data = {}
        for doc in docs:
            key = doc.get("_doc_key")
            if key:
                data[key] = doc.get("payload", {})
        return data
    except PyMongoError as e:
        raise HTTPException(status_code=500, detail=f"数据库读取失败: {e}")

def save_one(key: str, value: Any):
    try:
        col.replace_one({"_doc_key": key}, {"_doc_key": key, "payload": value}, upsert=True)
    except PyMongoError as e:
        raise HTTPException(status_code=500, detail=f"保存失败 [{key}]: {e}")

class ProjectRequest(BaseModel):
    name: str
    data: Any = None

class RestoreRequest(BaseModel):
    data: Any

@app.get("/")
def root():
    return {"status": "ok", "message": "INART PM API 运行中"}

@app.get("/projects")
def get_projects():
    data = load_all()
    result = {}
    for key, value in data.items():
        if key == "系统配置":
            continue
        proj = {k: v for k, v in value.items() if k not in ["配件清单长图"]}
        if "部件列表" in proj:
            comps = {}
            for c_name, c_data in proj["部件列表"].items():
                comp_light = {k: v for k, v in c_data.items() if k != "日志流"}
                logs_light = [{k: v for k, v in log.items() if k != "图片"} for log in c_data.get("日志流", [])]
                comp_light["日志流"] = logs_light
                comps[c_name] = comp_light
            proj["部件列表"] = comps
        result[key] = proj
    return result

@app.post("/project/get")
def get_project(req: ProjectRequest):
    data = load_all()
    if req.name not in data:
        raise HTTPException(status_code=404, detail=f"项目不存在: {req.name}")
    return data[req.name]

@app.post("/project/save")
def save_project(req: ProjectRequest):
    save_one(req.name, req.data)
    return {"status": "ok", "project": req.name}

@app.post("/project/delete")
def delete_project(req: ProjectRequest):
    try:
        col.delete_one({"_doc_key": req.name})
        return {"status": "ok", "deleted": req.name}
    except PyMongoError as e:
        raise HTTPException(status_code=500, detail=str(e))

@app.get("/config")
def get_config():
    data = load_all()
    return data.get("系统配置", {})

@app.post("/config/save")
def save_config(req: ProjectRequest):
    save_one("系统配置", req.data)
    return {"status": "ok"}

@app.get("/backup")
def backup():
    return load_all()

@app.post("/restore")
def restore(req: RestoreRequest):
    for key, value in req.data.items():
        save_one(key, value)
    return {"status": "ok", "count": len(req.data)}

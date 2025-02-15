from fastapi import FastAPI, Request, HTTPException
from fastapi.responses import FileResponse, JSONResponse, StreamingResponse, Response
from fastapi.staticfiles import StaticFiles
import sqlite3
import random
import time
import asyncio
import aiohttp
import json

app = FastAPI()

# Mount static files
app.mount("/static", StaticFiles(directory="static"))

# SQLite DB initialization
conn = sqlite3.connect("pool.db", check_same_thread=False)
cursor = conn.cursor()
cursor.execute("""
CREATE TABLE IF NOT EXISTS api_keys (
    key TEXT PRIMARY KEY,
    add_time REAL,
    balance REAL,
    usage_count INTEGER
)
""")
conn.commit()

# Create logs table for recording completion calls
cursor.execute("""
CREATE TABLE IF NOT EXISTS logs (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    used_key TEXT,
    model TEXT,
    call_time REAL,
    input_tokens INTEGER,
    output_tokens INTEGER,
    total_tokens INTEGER
)
""")
conn.commit()

BASE_URL = "https://api.siliconflow.cn"  # adjust if needed


async def validate_key_async(api_key: str):
    headers = {"Authorization": f"Bearer {api_key}"}
    try:
        async with aiohttp.ClientSession() as session:
            async with session.get(
                "https://api.siliconflow.cn/v1/user/info", headers=headers, timeout=10
            ) as r:
                if r.status == 200:
                    data = await r.json()
                    return True, data.get("data", {}).get("totalBalance", 0)
                else:
                    data = await r.json()
                    return False, data.get("message", "验证失败")
    except Exception as e:
        return False, f"请求失败: {str(e)}"


def insert_api_key(api_key: str, balance: float):
    cursor.execute(
        "INSERT OR IGNORE INTO api_keys (key, add_time, balance, usage_count) VALUES (?, ?, ?, ?)",
        (api_key, time.time(), balance, 0),
    )
    conn.commit()


def log_completion(
    used_key: str,
    model: str,
    call_time: float,
    input_tokens: int,
    output_tokens: int,
    total_tokens: int,
):
    cursor.execute(
        "INSERT INTO logs (used_key, model, call_time, input_tokens, output_tokens, total_tokens) VALUES (?, ?, ?, ?, ?, ?)",
        (used_key, model, call_time, input_tokens, output_tokens, total_tokens),
    )
    conn.commit()


@app.get("/")
async def root():
    return FileResponse("static/index.html")


@app.post("/import_keys")
async def import_keys(request: Request):
    data = await request.json()
    keys_text = data.get("keys", "")
    keys = [k.strip() for k in keys_text.splitlines() if k.strip()]
    if not keys:
        raise HTTPException(status_code=400, detail="未提供有效的api-key")
    tasks = []
    # Prepare tasks: for duplicate keys, add a dummy task returning a marker.
    for key in keys:
        cursor.execute("SELECT key FROM api_keys WHERE key = ?", (key,))
        if cursor.fetchone():
            tasks.append(asyncio.sleep(0, result=("duplicate", key)))
        else:
            tasks.append(validate_key_async(key))
    results = await asyncio.gather(*tasks)
    imported_count = 0
    duplicate_count = 0
    invalid_count = 0
    for idx, result in enumerate(results):
        if result[0] == "duplicate":
            duplicate_count += 1
        else:
            valid, balance = result
            if valid and float(balance) > 0:
                insert_api_key(keys[idx], balance)
                imported_count += 1
            else:
                invalid_count += 1
    return JSONResponse(
        {
            "message": f"导入成功 {imported_count} 个，有重复 {duplicate_count} 个，无效 {invalid_count} 个"
        }
    )


@app.post("/refresh")
async def refresh_keys():
    cursor.execute("SELECT key FROM api_keys")
    all_keys = [row[0] for row in cursor.fetchall()]

    # Create tasks for parallel validation
    tasks = [validate_key_async(key) for key in all_keys]
    results = await asyncio.gather(*tasks)

    removed = 0
    for key, (valid, balance) in zip(all_keys, results):
        if valid and float(balance) > 0:
            cursor.execute(
                "UPDATE api_keys SET balance = ? WHERE key = ?", (balance, key)
            )
        else:
            cursor.execute("DELETE FROM api_keys WHERE key = ?", (key,))
            removed += 1

    conn.commit()
    return JSONResponse(
        {"message": f"刷新完成，共移除 {removed} 个余额用尽或无效的key"}
    )


@app.post("/v1/chat/completions")
async def chat_completions(request: Request):
    cursor.execute("SELECT key FROM api_keys")
    keys = [row[0] for row in cursor.fetchall()]
    if not keys:
        raise HTTPException(status_code=500, detail="没有可用的api-key")
    selected = random.choice(keys)
    # Increase usage count
    cursor.execute(
        "UPDATE api_keys SET usage_count = usage_count + 1 WHERE key = ?", (selected,)
    )
    conn.commit()
    # Forward the request to BASE_URL using the selected key
    forward_headers = dict(request.headers)
    forward_headers["Authorization"] = f"Bearer {selected}"
    req_body = await request.body()
    req_json = await request.json()
    model = req_json.get("model", "unknown")
    # approximate input tokens as word count
    input_tokens = len(str(req_json).split())
    call_time_stamp = time.time()
    is_stream = req_json.get("stream", False)  # Changed default to False

    if is_stream:

        async def generate_stream():
            completion_tokens = 0
            async with aiohttp.ClientSession() as session:
                async with session.post(
                    f"{BASE_URL}/v1/chat/completions",
                    headers=forward_headers,
                    data=req_body,
                    timeout=30,
                ) as resp:
                    async for chunk in resp.content.iter_any():
                        try:
                            chunk_str = chunk.decode('utf-8')
                            if chunk_str == "[DONE]":
                                continue
                            if chunk_str.startswith('data: '):
                                data = json.loads(chunk_str[6:])
                                usage = data.get("usage", {})
                                completion_tokens = usage.get("completion_tokens", 0)
                        except Exception:
                            pass
                        yield chunk
            # 流结束后记录完整 token 数量
            log_completion(
                selected,
                model,
                call_time_stamp,
                input_tokens,
                completion_tokens,
                input_tokens + completion_tokens,
            )

        try:
            return StreamingResponse(
                generate_stream(),
                headers={"Content-Type": "application/octet-stream"},
            )
        except Exception as e:
            raise HTTPException(status_code=500, detail=f"请求转发失败: {str(e)}")
    else:
        try:
            async with aiohttp.ClientSession() as session:
                async with session.post(
                    f"{BASE_URL}/v1/chat/completions",
                    headers=forward_headers,
                    data=req_body,
                    timeout=30,
                ) as resp:
                    resp_json = await resp.json()
                    usage = resp_json.get("usage", {})
                    prompt_tokens = usage.get("prompt_tokens", 0)
                    completion_tokens = usage.get("completion_tokens", 0)
                    total_tokens = usage.get("total_tokens", 0)
                    # Log completion call
                    log_completion(
                        selected,
                        model,
                        call_time_stamp,
                        input_tokens or prompt_tokens,
                        completion_tokens,
                        total_tokens,
                    )
                    return JSONResponse(content=resp_json, status_code=resp.status)
        except aiohttp.ClientConnectionError as e:
            raise HTTPException(status_code=502, detail=f"连接关闭: {str(e)}")
        except asyncio.TimeoutError as e:
            raise HTTPException(status_code=504, detail=f"请求超时: {str(e)}")
        except Exception as e:
            raise HTTPException(status_code=500, detail=f"请求转发失败: {str(e)}")


@app.get("/v1/models")
async def list_models(request: Request):
    cursor.execute("SELECT key FROM api_keys")
    keys = [row[0] for row in cursor.fetchall()]
    if not keys:
        raise HTTPException(status_code=500, detail="没有可用的api-key")
    selected = random.choice(keys)
    forward_headers = dict(request.headers)
    forward_headers["Authorization"] = f"Bearer {selected}"
    try:
        async with aiohttp.ClientSession() as session:
            async with session.get(
                f"{BASE_URL}/v1/models", headers=forward_headers, timeout=30
            ) as resp:
                data = await resp.json()
                return JSONResponse(content=data, status_code=resp.status)
    except Exception as e:
        raise HTTPException(status_code=500, detail=f"请求转发失败: {str(e)}")


@app.get("/stats")
async def stats():
    cursor.execute("SELECT COUNT(*), COALESCE(SUM(balance), 0) FROM api_keys")
    count, total_balance = cursor.fetchone()
    return JSONResponse({"key_count": count, "total_balance": total_balance})


@app.get("/export_keys")
async def export_keys():
    cursor.execute("SELECT key FROM api_keys")
    all_keys = cursor.fetchall()
    keys = "\n".join(row[0] for row in all_keys)
    headers = {"Content-Disposition": "attachment; filename=keys.txt"}
    return Response(content=keys, media_type="text/plain", headers=headers)


@app.get("/logs")
async def get_logs(page: int = 1):
    page_size = 10
    offset = (page - 1) * page_size
    cursor.execute("SELECT COUNT(*) FROM logs")
    total = cursor.fetchone()[0]
    cursor.execute(
        "SELECT used_key, model, call_time, input_tokens, output_tokens, total_tokens FROM logs ORDER BY call_time DESC LIMIT ? OFFSET ?",
        (page_size, offset),
    )
    logs = cursor.fetchall()
    # Format logs as list of dicts
    log_list = [
        {
            "used_key": row[0],
            "model": row[1],
            "call_time": row[2],
            "input_tokens": row[3],
            "output_tokens": row[4],
            "total_tokens": row[5],
        }
        for row in logs
    ]
    return JSONResponse(
        {"logs": log_list, "total": total, "page": page, "page_size": page_size}
    )


@app.post("/clear_logs")
async def clear_logs():
    try:
        cursor.execute("DELETE FROM logs")
        conn.commit()
        cursor.execute("VACUUM")
        conn.commit()
        return JSONResponse({"message": "日志已清空"})
    except Exception as e:
        raise HTTPException(status_code=500, detail=f"清空日志失败: {str(e)}")


if __name__ == "__main__":
    import uvicorn

    uvicorn.run(app, host="127.0.0.1", port=7898)

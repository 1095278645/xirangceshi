"""数据备份 / 导出 / 恢复（财务数据不可丢失）

恢复入口有两个，覆盖两种真实场景：
  - POST /api/backup/restore/{name}：从**备份列表里选一份**恢复（最常用）
  - POST /api/backup/import：上传一个 zip/.db 恢复（换机器、从网盘恢复）

import 走**原始请求体**而不是 multipart 文件上传：后者需要 python-multipart
依赖，而这里只是传一个 zip 字节流，用裸 body 更简单也少一个依赖：
    curl --data-binary @backup.zip "http://127.0.0.1:8000/api/backup/import?confirm=true"
"""
import logging
from pathlib import Path

from fastapi import APIRouter, HTTPException, Query, Request
from fastapi.responses import FileResponse

import backup

router = APIRouter(prefix="/api", tags=["backup"])
log = logging.getLogger("routers.backup")

# 上传文件大小上限（防止把磁盘写满）；备份包通常几百 KB ~ 几十 MB
MAX_UPLOAD_BYTES = 256 * 1024 * 1024


def _safe_backup_path(name: str) -> Path:
    """把请求里的文件名收敛到备份目录内，防目录穿越。"""
    base = backup._backup_dir().resolve()
    p = (backup._backup_dir() / name).resolve()
    if base not in p.parents and p != base:
        raise HTTPException(400, "非法的备份文件名")
    if not p.is_file():
        raise HTTPException(404, "备份不存在")
    return p


@router.get("/backup/list")
def backup_list():
    """备份列表（含大小/时间/类型）。"""
    return {"backups": backup.list_backups(), "dir": str(backup._backup_dir()),
            "db_size": backup.db_size()}


@router.post("/backup/create")
def backup_create(note: str = Query(default="", max_length=200)):
    """手动创建一份备份。"""
    info = backup.create_backup("manual", note=note)
    return {"ok": True, "backup": info}


@router.get("/backup/download/{name}")
def backup_download(name: str):
    """下载某个备份文件（可用于异地保存）。"""
    p = _safe_backup_path(name)
    return FileResponse(str(p), filename=p.name)


@router.delete("/backup/{name}")
def backup_delete(name: str):
    p = _safe_backup_path(name)
    p.unlink()
    return {"ok": True, "deleted": name}


@router.post("/backup/export")
def backup_export(include_config: bool = Query(default=False)):
    """导出一键备份包（zip，含数据库 + manifest）。

    include_config=False（默认）不含 API Key，便于把备份包发给别人或存到网盘。
    """
    info = backup.export_bundle(include_config=include_config)
    return {"ok": True, "export": info}


@router.post("/backup/import")
async def backup_import(request: Request, confirm: bool = Query(default=False),
                        filename: str = Query(default="upload.zip")):
    """用**请求体里的 zip/.db 字节流**恢复数据（零额外依赖）。

    危险操作：会**覆盖当前全部数据**，因此要求显式 confirm=true；
    恢复前会自动把当前库另存为 pre_restore 快照（可退回）。
    """
    if not confirm:
        raise HTTPException(400, "恢复会覆盖当前全部数据，请传 confirm=true 明确确认")
    raw = await request.body()
    if not raw:
        raise HTTPException(400, "请求体为空（请用 --data-binary 传备份文件）")
    if len(raw) > MAX_UPLOAD_BYTES:
        raise HTTPException(413, "上传文件过大（上限 256MB）")

    backup._ensure_dir()
    suffix = Path(filename).suffix or ".zip"
    if suffix not in (".zip", ".db"):
        raise HTTPException(400, "只接受 .zip 或 .db 备份文件")
    upload = backup._backup_dir() / f"upload-{backup._now_stamp()}{suffix}"
    upload.write_bytes(raw)
    try:
        result = backup.restore_from_bundle(upload)
    except ValueError as e:
        raise HTTPException(400, f"恢复失败：{e}") from e
    except Exception as e:  # noqa: BLE001
        log.exception("恢复失败")
        raise HTTPException(500, f"恢复失败：{e}") from e
    finally:
        # 上传的临时文件不保留（内容已进库；需要留档请用备份/导出功能）
        try:
            upload.unlink(missing_ok=True)
        except OSError:
            pass
    return result


@router.post("/backup/restore/{name}")
def backup_restore(name: str, confirm: bool = Query(default=False)):
    """用备份目录中已有的某份备份恢复（最常用：先列备份，再选一份恢复）。"""
    if not confirm:
        raise HTTPException(400, "恢复会覆盖当前全部数据，请传 confirm=true 明确确认")
    p = _safe_backup_path(name)
    try:
        return backup.restore_from_bundle(p)
    except ValueError as e:
        raise HTTPException(400, f"恢复失败：{e}") from e

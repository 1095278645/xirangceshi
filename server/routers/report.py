"""报表导出（省账通能力）"""
from pathlib import Path

from fastapi import APIRouter, HTTPException
from fastapi.responses import FileResponse

import report as reportlib

router = APIRouter(prefix="/api", tags=["report"])


@router.get("/report/monthly")
def report_monthly(year: int | None = None, month: int | None = None):
    """导出月度收支 Excel 报表"""
    result = reportlib.get_monthly_report(year, month)
    if "error" in result:
        raise HTTPException(500, result["error"])
    return FileResponse(result["file"], filename=Path(result["file"]).name)
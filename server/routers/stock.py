"""库存进销存域：商品档案 / 入库出库盘点 / 补货与过期预警（亲民口径）"""
import logging

from fastapi import APIRouter

import db
from schemas import ProductIn, StockMoveIn

log = logging.getLogger("stock")
router = APIRouter(prefix="/api", tags=["stock"])


@router.get("/stock")
def stock_overview():
    """库存总览（亲民：货值 / 快补货 / 快过期）"""
    return db.stock_summary()


@router.get("/products")
def products():
    """商品清单"""
    return db.list_products()


@router.post("/products")
def save_product(data: ProductIn):
    """新增/更新商品"""
    if data.pid:
        ok = db.update_product(
            data.pid, name=data.name, category=data.category, unit=data.unit,
            safety_stock=data.safety_stock, unit_cost=data.unit_cost,
            expiry_date=data.expiry_date, supplier=data.supplier, note=data.note)
        return {"product_id": data.pid, "updated": ok}
    pid = db.add_product(data.name, data.category, data.unit, data.stock_qty,
                         data.safety_stock, data.unit_cost, data.expiry_date,
                         data.supplier, data.note)
    return {"product_id": pid, "saved": True}


@router.delete("/products/{pid}")
def remove_product(pid: int):
    return {"deleted": db.delete_product(pid)}


@router.post("/products/{pid}/move")
def move_stock(pid: int, data: StockMoveIn):
    """入库/出库/盘点并自动更新库存（亲民：进了多少货 / 出了多少）"""
    r = db.move_stock(pid, data.movement, data.qty, data.note)
    return r or {"error": "product not found"}
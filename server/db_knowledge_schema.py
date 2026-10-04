"""db_knowledge_schema.py — 知识资产治理层建表 DDL

为什么单独一个文件：db_schema.py 已接近架构自检的 400 行硬上限，
这里只做"搬家"——把知识资产两张表的 DDL 收进来，`init_knowledge_tables(conn)`
接收连接并建表，由 `db_schema.init_schema` 在末尾调用（幂等 CREATE TABLE IF NOT EXISTS）。

两张表的分工：
  - `knowledge_assets`：知识资产本体（来源 / 版本 / 状态 / 挥发度 / 核验结果）。
    状态**实现上**支持：active（在用）/ draft（草稿，不顶掉在用结论）/ superseded（被新版替代）
    / retired（退休，暂无写入口）。另有一条**部分唯一索引**保证每个 asset_key 至多一条
    在用/草稿行（并发登记的兜底，见 knowledge_assets.register_asset 的 BEGIN IMMEDIATE）。
  - `knowledge_edges`：跨域关系索引的**存储**（谁和谁有什么关系）。
    本层只负责建表，边的抽取/合并/回收逻辑在关系索引层实现。

不改动任何既有表；老库升级靠 db.SCHEMA_VERSION +1 触发 init_schema 重跑。
"""
from __future__ import annotations

__all__ = ["init_knowledge_tables"]


def init_knowledge_tables(conn) -> None:
    """建两张表（幂等）与索引：knowledge_assets / knowledge_edges"""
    conn.executescript("""
    CREATE TABLE IF NOT EXISTS knowledge_assets (
        asset_id      TEXT PRIMARY KEY,          -- AS-YYYYMMDD-XXX
        asset_key     TEXT NOT NULL,             -- 业务键（默认 kind:subject），版本链的锚点
        version       INTEGER NOT NULL DEFAULT 1,
        kind          TEXT NOT NULL,             -- decision/experience/strategy/attention/fact/profile
        state         TEXT NOT NULL DEFAULT 'draft',      -- draft/active/superseded/retired
        volatility    TEXT NOT NULL DEFAULT 'volatile',   -- stable/slow/volatile
        subject       TEXT DEFAULT '',
        statement     TEXT NOT NULL,             -- 知识正文（结论本身）
        evidence_json TEXT DEFAULT '[]',         -- 支撑事实（可回查的原始依据）
        source_kind   TEXT DEFAULT '',           -- 来源类型，决定默认挥发度
        source_ref    TEXT DEFAULT '',           -- 来源定位（如 ledger:daily_review）
        confidence    REAL DEFAULT 0.5,
        drift_note    TEXT DEFAULT '',           -- 运行期核验的人话说明
        created_at    TEXT DEFAULT (datetime('now','localtime')),
        updated_at    TEXT DEFAULT (datetime('now','localtime')),
        verified_at   TEXT DEFAULT '',           -- 上次运行期核验时间
        verify_ok     INTEGER DEFAULT 1,         -- 1=对得上真值 / 0=漂移或无法核验
        valid_from    TEXT DEFAULT '',           -- 本版本生效时间
        valid_to      TEXT DEFAULT '',           -- 本版本失效时间（被取代/退休时写）
        superseded_by TEXT DEFAULT ''            -- 取代它的 asset_id
    );

    CREATE INDEX IF NOT EXISTS idx_knowledge_assets_key
        ON knowledge_assets(asset_key, version DESC);
    CREATE INDEX IF NOT EXISTS idx_knowledge_assets_state
        ON knowledge_assets(state, kind);

    CREATE TABLE IF NOT EXISTS knowledge_edges (
        edge_id      TEXT PRIMARY KEY,           -- ED-... 内容寻址/序号
        edge_key     TEXT NOT NULL,              -- 业务键（三元组哈希），增量合并的锚点
        subject_type TEXT NOT NULL DEFAULT '',   -- customer/product/invoice/...
        subject_ref  TEXT NOT NULL DEFAULT '',
        subject_label TEXT DEFAULT '',           -- 给人看的名字
        relation     TEXT NOT NULL,              -- 关系类型（如 熟客→常买）
        object_type  TEXT NOT NULL DEFAULT '',
        object_ref   TEXT NOT NULL DEFAULT '',
        object_label TEXT DEFAULT '',
        evidence     TEXT DEFAULT '',            -- 佐证（原始单据定位）
        confidence   REAL DEFAULT 0.5,
        active       INTEGER DEFAULT 1,          -- 0=软删（回收后仍可回溯）
        first_seen   TEXT DEFAULT '',
        last_seen    TEXT DEFAULT '',
        valid_from   TEXT DEFAULT '',
        valid_to     TEXT DEFAULT ''
    );

    -- edge_key 上**不加 UNIQUE**：关系层要能统计 duplicate_edge_keys（同键多行）。
    CREATE INDEX IF NOT EXISTS idx_knowledge_edges_key
        ON knowledge_edges(edge_key);
    CREATE INDEX IF NOT EXISTS idx_knowledge_edges_subject
        ON knowledge_edges(subject_type, subject_ref, active);
    CREATE INDEX IF NOT EXISTS idx_knowledge_edges_object
        ON knowledge_edges(object_type, object_ref, active);
    CREATE INDEX IF NOT EXISTS idx_knowledge_edges_relation
        ON knowledge_edges(relation, active);
    """)
    # 每个 asset_key 最多一条"在用"（active/draft）—— 由数据库兜底并发登记：
    # 应用层的"先查再写"没有事务级唯一性，两个线程可同时走"没找到 active"分支，
    # 造出两条 version=1 的 active 行（独立复核用 barrier 放大窗口后实测复现）。
    conn.execute(
        "CREATE UNIQUE INDEX IF NOT EXISTS idx_knowledge_assets_one_active "
        "ON knowledge_assets(asset_key) WHERE state IN ('active','draft')")
    # 旧库迁移：把 UNIQUE 之前遗留的重复 active 行收敛成一条（保留最新，其余标 superseded）
    try:
        conn.execute(
            "UPDATE knowledge_assets SET state='superseded', "
            "valid_to=COALESCE(NULLIF(valid_to,''), datetime('now','localtime')) "
            "WHERE rowid IN ("
            "  SELECT rowid FROM (SELECT rowid, ROW_NUMBER() OVER ("
            "    PARTITION BY asset_key ORDER BY version DESC, rowid DESC) rn"
            "    FROM knowledge_assets WHERE state IN ('active','draft')) WHERE rn > 1)")
    except Exception:  # noqa: BLE001 —— 老库可能没有该表列组合，迁移失败不阻断建表
        pass

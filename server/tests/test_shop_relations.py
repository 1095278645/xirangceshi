# -*- coding: utf-8 -*-
"""跨域关系索引层测试：建表幂等 / upsert 幂等 / 软删 / BFS 邻域 / 路径 / 体检 / 增量合并护栏。

重点（对应参考图「看得懂」）：
  - 建表幂等且与 db_knowledge_schema 侧一致，测试**不依赖**建表队友进度（夹具显式建表）；
  - upsert 按 edge_key 幂等：内容没变只刷 last_seen，变内容才更新，被回收的能复活；
  - 关系只软删（行还在、valid_to 有值），物理清理只删"已回收且过保留期"的；
  - BFS/最短路只走 active 边，防环，路径最多 3 条；
  - graph_health 能识别脏数据（空引用 / 重复 key / 陈旧边）；
  - **护栏**：抽取为空时 merge 不得清空索引（专门用例）。

运行：cd server && python -m pytest tests/test_shop_relations.py -q
"""
import sys
import tempfile
import unittest
from datetime import datetime, timedelta
from pathlib import Path
from unittest import mock

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

import db
import db_relations
import shop_relations

OLD = (datetime.now() - timedelta(days=400)).strftime("%Y-%m-%d %H:%M:%S")
SENTINEL = "2020-01-01 00:00:00"


def _mk_edge(st="customer", sref="1", rel="purchased", ot="transaction", oref="1",
             slabel="老王", olabel="交易#1", evidence="交易#1 2026-10-01 12.00 元 肉包",
             confidence=0.9) -> dict:
    return {"subject_type": st, "subject_ref": sref, "subject_label": slabel,
            "relation": rel, "object_type": ot, "object_ref": oref,
            "object_label": olabel, "evidence": evidence, "confidence": confidence}


class _Base(unittest.TestCase):
    def setUp(self):
        self._tmp = tempfile.TemporaryDirectory()
        db.DB_PATH = Path(self._tmp.name) / "relations.db"
        db._schema_ready.clear()
        self.addCleanup(db._schema_ready.clear)
        db.init_db()
        # 关系层独立可跑：显式建表，不等 db_knowledge_schema 的合入时点
        with db.get_conn() as conn:
            db_relations.init_relations_tables(conn)

    def tearDown(self):
        self._tmp.cleanup()

    def raw(self, sql, params=()):
        with db.get_conn() as conn:
            conn.execute(sql, params)


class TestInitTables(_Base):
    def test_init_is_idempotent_and_creates_table_and_indexes(self):
        with db.get_conn() as conn:
            db_relations.init_relations_tables(conn)   # 第二次不应报错
            cols = [r["name"] for r in conn.execute("PRAGMA table_info(knowledge_edges)")]
            idx = [r["name"] for r in conn.execute("PRAGMA index_list(knowledge_edges)")]
        self.assertEqual(cols[0], "edge_id")
        for col in ("edge_key", "subject_type", "subject_ref", "subject_label", "relation",
                    "object_type", "object_ref", "object_label", "evidence", "confidence",
                    "active", "first_seen", "last_seen", "valid_from", "valid_to"):
            self.assertIn(col, cols)
        for name in ("idx_knowledge_edges_key", "idx_knowledge_edges_subject",
                     "idx_knowledge_edges_object", "idx_knowledge_edges_relation"):
            self.assertIn(name, idx)


class TestUpsertEdge(_Base):
    def test_created_then_unchanged_refreshes_last_seen_only(self):
        first = db_relations.upsert_edge(_mk_edge())
        self.assertEqual(first["action"], "created")
        self.assertTrue(first["edge_id"])
        self.raw("UPDATE knowledge_edges SET last_seen=? WHERE edge_id=?", (SENTINEL, first["edge_id"]))
        again = db_relations.upsert_edge(_mk_edge())
        self.assertEqual(again["action"], "unchanged")
        self.assertEqual(again["edge_id"], first["edge_id"])
        row = db_relations.get_edge(first["edge_id"])
        self.assertNotEqual(row["last_seen"], SENTINEL)      # 只刷新 last_seen
        self.assertTrue(row["active"])
        self.assertEqual(row["evidence"], _mk_edge()["evidence"])

    def test_updated_keeps_first_seen_and_valid_from(self):
        created = db_relations.upsert_edge(_mk_edge())
        self.raw("UPDATE knowledge_edges SET first_seen=?, valid_from=? WHERE edge_id=?",
                 (SENTINEL, SENTINEL, created["edge_id"]))
        upd = db_relations.upsert_edge(_mk_edge(evidence="交易#1 2026-10-02 15.00 元 豆浆"))
        self.assertEqual(upd["action"], "updated")
        row = db_relations.get_edge(created["edge_id"])
        self.assertEqual(row["first_seen"], SENTINEL)        # 首次时间保留
        self.assertEqual(row["valid_from"], SENTINEL)
        self.assertIn("豆浆", row["evidence"])

    def test_revived_reactivates_and_clears_valid_to(self):
        created = db_relations.upsert_edge(_mk_edge())
        self.assertEqual(db_relations.mark_edges_inactive([created["edge_key"]]), 1)
        rev = db_relations.upsert_edge(_mk_edge(evidence="交易#1 2026-10-03 18.00 元 肉包"))
        self.assertEqual(rev["action"], "revived")
        row = db_relations.get_edge(created["edge_id"])
        self.assertTrue(row["active"])
        self.assertEqual(row["valid_to"], "")
        self.assertIn("18.00", row["evidence"])

    def test_edge_key_autocomputed_when_missing(self):
        e = _mk_edge()
        got = db_relations.upsert_edge(e)
        expect = shop_relations.edge_key(e["subject_type"], e["subject_ref"], e["relation"],
                                         e["object_type"], e["object_ref"])
        self.assertEqual(got["edge_key"], expect)
        self.assertNotIn("edge_key", e)     # 不改调用方入参


class TestSoftDelete(_Base):
    def test_mark_inactive_keeps_row_and_sets_valid_to(self):
        created = db_relations.upsert_edge(_mk_edge())
        n = db_relations.mark_edges_inactive([created["edge_key"]])
        self.assertEqual(n, 1)
        self.assertEqual(db_relations.mark_edges_inactive([created["edge_key"]]), 0)  # 不重复计数
        rows = db_relations.list_edges(active=False)
        self.assertEqual(len(rows), 1)                      # 行还在（软删）
        self.assertFalse(rows[0]["active"])
        self.assertTrue(rows[0]["valid_to"])
        self.assertIsNotNone(db_relations.get_edge(created["edge_id"]))


class TestListEdges(_Base):
    def test_filters_limit_and_cap(self):
        for i in range(3):
            db_relations.upsert_edge(_mk_edge(oref=str(i + 1)))
        db_relations.upsert_edge(_mk_edge(rel="mentions", ot="product", oref="包子"))
        self.assertEqual(len(db_relations.list_edges()), 4)
        self.assertEqual(len(db_relations.list_edges(relation="mentions")), 1)
        self.assertEqual(len(db_relations.list_edges(subject_type="customer")), 4)
        self.assertEqual(len(db_relations.list_edges(relation="purchased", active=True)), 3)
        self.assertEqual(len(db_relations.list_edges(limit=2)), 2)
        self.assertEqual(len(db_relations.list_edges(limit=99999)), 4)   # 上限被夹住但不报错
        self.assertEqual(db_relations.list_edges(active=True)[0]["active"], True)
        self.assertIsInstance(db_relations.list_edges(active=True)[0]["confidence"], float)


class TestNeighborsAndPaths(_Base):
    def _fanout(self, n=4):
        """customer/1 -> transaction/i -> product/包子（n 条等长两跳路径）。"""
        for i in range(1, n + 1):
            db_relations.upsert_edge(_mk_edge(oref=str(i), olabel=f"交易#{i}"))
            db_relations.upsert_edge(_mk_edge(st="product", sref="包子", slabel="包子",
                                              rel="sold_in", ot="transaction", oref=str(i),
                                              olabel=f"交易#{i}"))

    def test_neighbors_bfs_depths_and_dedup(self):
        self._fanout(2)
        nodes = db_relations.neighbors("customer", "1", depth=2)
        pair = [(n["type"], n["ref"]) for n in nodes]
        self.assertEqual(nodes[0]["depth"], 0)
        self.assertEqual(nodes[0]["label"], "老王")
        self.assertEqual(pair.count(("product", "包子")), 1)          # 不重复
        self.assertEqual(len(pair), len(set(pair)))
        depth1 = {n["ref"] for n in nodes if n["depth"] == 1}
        self.assertEqual(depth1, {"1", "2"})
        product = [n for n in nodes if n["type"] == "product"][0]
        self.assertEqual(product["depth"], 2)
        self.assertEqual(product["via_relation"], "sold_in")
        self.assertIn(product["from_ref"], {"1", "2"})
        self.assertEqual(len(db_relations.neighbors("customer", "1", depth=1)), 3)  # 起点+2

    def test_neighbors_skips_inactive_edges(self):
        created = db_relations.upsert_edge(_mk_edge())
        db_relations.mark_edges_inactive([created["edge_key"]])
        self.assertEqual([n["depth"] for n in db_relations.neighbors("customer", "1", depth=2)],
                         [0])

    def test_find_paths_shortest_and_no_path(self):
        self._fanout(1)
        paths = db_relations.find_paths("customer", "1", "product", "包子", max_depth=3)
        self.assertEqual(len(paths), 1)
        self.assertEqual(len(paths[0]), 2)
        self.assertEqual([s["type"] for s in paths[0]], ["transaction", "product"])
        self.assertEqual([s["relation"] for s in paths[0]], ["purchased", "sold_in"])
        self.assertEqual(db_relations.find_paths("customer", "1", "debt", "9"), [])
        self.assertEqual(db_relations.find_paths("customer", "1", "customer", "1"), [])

    def test_find_paths_caps_at_three_and_survives_cycle(self):
        self._fanout(5)
        paths = db_relations.find_paths("customer", "1", "product", "包子", max_depth=3)
        self.assertEqual(len(paths), 3)                              # 前 3 条
        self.assertTrue(all(len(p) == 2 for p in paths))
        # 造环 A-B-A：不应死循环，最短路径仍是 2 跳
        db_relations.upsert_edge(_mk_edge(rel="linked_transaction", ot="customer", oref="1",
                                          olabel="老王"))
        paths2 = db_relations.find_paths("customer", "1", "product", "包子", max_depth=3)
        self.assertEqual(len(paths2), 3)
        self.assertTrue(all(len(p) == 2 for p in paths2))


class TestStatsAndHealth(_Base):
    def test_relation_stats_self_consistent(self):
        db_relations.upsert_edge(_mk_edge())
        db_relations.upsert_edge(_mk_edge(rel="mentions", ot="product", oref="包子",
                                          olabel="包子"))
        old = db_relations.upsert_edge(_mk_edge(oref="9", olabel="交易#9"))
        db_relations.mark_edges_inactive([old["edge_key"]])
        s = db_relations.relation_stats()
        self.assertEqual(s["edges_total"], 3)
        self.assertEqual(s["edges_active"], 2)
        self.assertEqual(s["by_relation"], {"purchased": 1, "mentions": 1})
        self.assertEqual(s["by_type_pair"]["customer→transaction"], 1)
        self.assertTrue(s["first_seen_min"] and s["last_seen_max"])

    def test_graph_health_detects_dirty_rows(self):
        db_relations.upsert_edge(_mk_edge())
        clean = db_relations.graph_health()
        self.assertTrue(clean["ok"])
        self.assertEqual(clean["orphan_edges"], 0)
        self.raw("INSERT INTO knowledge_edges(edge_id, edge_key, subject_type, subject_ref, "
                 "relation, object_type, object_ref, active, last_seen) "
                 "VALUES('dirty-1','sha256:dirty','customer','1','purchased','transaction','',1,?)",
                 (datetime.now().strftime("%Y-%m-%d %H:%M:%S"),))
        bad = db_relations.graph_health()
        self.assertGreaterEqual(bad["orphan_edges"], 1)
        self.assertGreaterEqual(bad["dangling_objects"], 1)
        self.assertFalse(bad["ok"])
        # 重复 edge_key（破坏幂等）+ 陈旧边
        self.raw("INSERT INTO knowledge_edges(edge_id, edge_key, subject_type, subject_ref, "
                 "relation, object_type, object_ref, active, last_seen) "
                 "VALUES('dup-1','sha256:dirty','customer','1','purchased','transaction','2',1,?)",
                 (OLD,))
        dup = db_relations.graph_health()
        self.assertGreaterEqual(dup["duplicate_edge_keys"], 1)
        self.assertGreaterEqual(dup["stale_edges"], 1)
        self.assertFalse(dup["ok"])


class TestPrune(_Base):
    def test_prune_only_expired_inactive_edges(self):
        keep = db_relations.upsert_edge(_mk_edge(oref="1"))          # 刚回收 → 保留
        gone = db_relations.upsert_edge(_mk_edge(oref="2"))          # 超期回收 → 物理删
        live = db_relations.upsert_edge(_mk_edge(oref="3"))          # active → 绝不删
        db_relations.mark_edges_inactive([keep["edge_key"], gone["edge_key"], live["edge_key"]])
        self.raw("UPDATE knowledge_edges SET active=1, valid_to='' WHERE edge_id=?",
                 (live["edge_id"],))
        self.raw("UPDATE knowledge_edges SET valid_to=? WHERE edge_id=?", (OLD, gone["edge_id"]))
        self.raw("UPDATE knowledge_edges SET valid_to=? WHERE edge_id=?", (OLD, live["edge_id"]))
        self.assertEqual(db_relations.prune_edges(days=90), 1)
        self.assertIsNotNone(db_relations.get_edge(keep["edge_id"]))
        self.assertIsNotNone(db_relations.get_edge(live["edge_id"]))
        self.assertIsNone(db_relations.get_edge(gone["edge_id"]))

    def test_prune_uses_config_default_when_days_is_none(self):
        created = db_relations.upsert_edge(_mk_edge())
        db_relations.mark_edges_inactive([created["edge_key"]])
        self.assertEqual(db_relations.prune_edges(), 0)             # 默认保留期内不删


class TestEdgeKey(unittest.TestCase):
    def test_deterministic_and_relation_sensitive(self):
        a = shop_relations.edge_key("customer", "1", "purchased", "transaction", "1")
        b = shop_relations.edge_key("customer", "1", "purchased", "transaction", "1")
        c = shop_relations.edge_key("customer", "1", "mentions", "transaction", "1")
        self.assertEqual(a, b)
        self.assertNotEqual(a, c)
        self.assertTrue(a.startswith("sha256:"))
        self.assertEqual(len(a), len("sha256:") + 32)
        self.assertEqual(shop_relations.edge_key(None, "1", "purchased", "transaction", "1"),
                         shop_relations.edge_key("", "1", "purchased", "transaction", "1"))


class TestExtractAndMerge(_Base):
    def _seed(self):
        ids = []
        for name in ("老王", "小李"):
            cid, _ = db.find_or_create_customer(name)
            ids.append(cid)
        for i, cid in enumerate(ids):
            db.add_transaction(cid, "肉包 2 个", 12.0 + i, "income", "主营业务收入")
        db.add_transaction(ids[0], "豆浆 1 杯", 3.0, "income", "主营业务收入")
        return ids

    def test_merge_end_to_end_then_second_merge_is_incremental(self):
        ids = self._seed()
        first = shop_relations.merge_relations()
        self.assertEqual(first["scanned"], 3)                     # 如实说明扫了 3 笔
        self.assertEqual(first["created"], 3)
        self.assertEqual(first["deactivated"], 0)
        self.assertNotIn("skipped", first)
        purchased = db_relations.list_edges(subject_type="customer", relation="purchased",
                                            active=True)
        self.assertEqual(len(purchased), 3)
        self.assertIn(str(ids[0]), {e["subject_ref"] for e in purchased})
        for e in purchased:
            self.assertIn("交易#", e["evidence"])                 # 证据可回查
        second = shop_relations.merge_relations()
        self.assertGreater(second["unchanged"], 0)
        self.assertEqual(second["deactivated"], 0)                # 增量幂等：不该回收任何边
        self.assertEqual(second["active"], first["active"])
        self.assertEqual(shop_relations.last_extract_stats()["transactions_scanned"], 3)

    def test_merge_guard_keeps_index_when_extraction_is_empty(self):
        self._seed()
        shop_relations.merge_relations()
        before = db_relations.relation_stats()["edges_active"]
        self.assertGreater(before, 0)
        self.raw("DELETE FROM transactions")                      # 数据抖动：抽取变空
        res = shop_relations.merge_relations()
        self.assertEqual(res.get("skipped"), "no_edges_extracted")
        self.assertEqual(res["deactivated"], 0)
        self.assertEqual(res["created"], 0)
        self.assertEqual(db_relations.relation_stats()["edges_active"], before)   # 索引没被清空
        self.assertIn("护栏", shop_relations.merge_relations.__doc__)

    def test_full_without_confirm_never_wipes_the_index(self):
        """铁律2：回收是破坏性动作，`full=true` 但没 `confirm=true` 时一条都不许软删。

        这条是独立复核发现的真缺陷：早期实现里 `full=true` 能绕过"空结果不清库"的护栏，
        一次数据抖动 + 一个 query 参数就能把整张关系网抹平。
        """
        self._seed()
        shop_relations.merge_relations()
        # 造一条"这次抽不到"的孤儿边（模拟窗口滑出/单据被删）——它是唯一会被回收的对象。
        # 注意 edge_key 由数据层按五元组自己算（传进去的 edge_key 会被忽略），
        # 所以这里从库里反查真实 key，别自己拼一个。
        db_relations.upsert_edge({
            "subject_type": "customer", "subject_ref": "777", "subject_label": "老客",
            "relation": "purchased", "object_type": "transaction", "object_ref": "666",
            "object_label": "交易#666", "evidence": "历史边", "confidence": 0.5})
        orphan_key = next(
            e["edge_key"] for e in db_relations.get_edges_by_keys(
                db_relations.active_edge_keys()) if e["subject_ref"] == "777")
        before = db_relations.relation_stats()["edges_active"]
        self.assertGreater(before, 0)
        # 抽出来的是"别的东西"→ 孤儿边不在场，正是会被回收的场景
        other = [{"subject_type": "customer", "subject_ref": "999", "subject_label": "别人",
                  "relation": "purchased", "object_type": "transaction", "object_ref": "888",
                  "object_label": "交易#888", "evidence": "手工边", "confidence": 0.5}]
        with mock.patch.object(shop_relations, "extract_edges", return_value=other):
            res = shop_relations.merge_relations(full=True)          # 少了 confirm
        self.assertEqual(res["deactivated"], 0)
        self.assertEqual(res.get("skipped"), "confirm_required")
        self.assertEqual(res.get("pending_recycle"), before)         # 明确告知"有几条该收没收"
        self.assertGreaterEqual(db_relations.relation_stats()["edges_active"], before)
        # 抽取为空时即使 full+confirm 也照样不动（护栏①优先级最高）
        with mock.patch.object(shop_relations, "extract_edges", return_value=[]):
            res2 = shop_relations.merge_relations(full=True, confirm=True)
        self.assertEqual(res2.get("skipped"), "no_edges_extracted")
        self.assertEqual(res2["deactivated"], 0)
        self.assertGreaterEqual(db_relations.relation_stats()["edges_active"], before)
        # 补齐 confirm=true 才允许回收：抽取与库里完全不同 → 原有的边全部不在场，会被回收
        with mock.patch.object(shop_relations, "extract_edges", return_value=other):
            confirmed = shop_relations.merge_relations(full=True, confirm=True)
        self.assertEqual(confirmed["deactivated"], before)
        row = db_relations.get_edges_by_keys([orphan_key])[0]
        self.assertFalse(row["active"])                           # 软删：行还在，只是失效
        self.assertTrue(row["valid_to"])


class TestSurfaces(_Base):
    def _seed(self):
        cid, _ = db.find_or_create_customer("老王")
        db.add_transaction(cid, "肉包 2 个", 12.0, "income", "主营业务收入")
        return cid

    def test_landscape_chain_checkpoint_and_summary(self):
        cid = self._seed()
        shop_relations.merge_relations()
        changed = shop_relations.relations_changed_since("")
        self.assertTrue(changed["changed"])                       # checkpoint 为空=全量已变
        self.assertTrue(changed["checkpoint"])
        again = shop_relations.relations_changed_since(changed["checkpoint"])
        self.assertFalse(again["changed"])                        # 没有新变化
        land = shop_relations.entity_landscape()
        self.assertIn("customer", land["nodes"])
        self.assertTrue(land["note"])
        self.assertTrue(land["checkpoint"])
        self.assertTrue(land["edges"][0]["relation_zh"] in shop_relations.RELATION_ZH.values())
        only = shop_relations.entity_landscape(keyword="老王")
        self.assertEqual([n["label"] for n in only["nodes"]["customer"]], ["老王"])
        chain = shop_relations.knowledge_chain("customer", str(cid), depth=2)
        self.assertEqual(chain["start"]["type"], "customer")
        self.assertIn("transaction", {n["type"] for n in chain["nodes"]})
        self.assertTrue(chain["paths"])
        summary = shop_relations.relations_summary()
        self.assertEqual(summary["relation_types"], list(shop_relations.RELATION_TYPES))
        self.assertIn("health", summary)
        self.assertTrue(summary["checkpoint"])

    def test_relations_types_frozen(self):
        self.assertEqual(shop_relations.ENTITY_TYPES,
                         ("customer", "product", "supplier", "invoice", "transaction",
                          "debt", "unknown"))
        self.assertEqual(set(shop_relations.RELATION_ZH), set(shop_relations.RELATION_TYPES))


if __name__ == "__main__":
    unittest.main(verbosity=2)

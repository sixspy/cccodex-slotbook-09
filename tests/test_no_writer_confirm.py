"""无写权确认零写入（甲路径：鉴权门闸零写入，零写与零审同态）。

红-红-绿 TDD：
- 漏洞态（环境变量 SLOTBOOK_VULN_GATE=1，夹具 monkeypatch 摘除确认口门闸，
  对应“只读访客点确认仍成功”的假绿现场）：两个测试均见红；
- 修复态（默认）：两个测试均见绿。

夹具全部跑本机文件库（tmp_path 下文件 SQLite，经 db.DB_PATH monkeypatch）。
"""
from __future__ import annotations

import os
import sys
from datetime import datetime
from pathlib import Path

import pytest
from fastapi.testclient import TestClient

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "backend"))

import confirm_svc  # noqa: E402
from app import app  # noqa: E402
import db as dbmod  # noqa: E402
from db import init_db, sha_password  # noqa: E402


@pytest.fixture(autouse=True)
def _fresh_db(tmp_path, monkeypatch):
    db = tmp_path / "t.db"
    monkeypatch.setattr(dbmod, "DB_PATH", db)
    if db.exists():
        db.unlink()
    init_db()
    yield


@pytest.fixture(autouse=True)
def _vuln_gate_off(monkeypatch):
    """假绿现场：确认写口未挂鉴权门闸（仅测试态注入，不改生产代码）。

    模拟原始漏洞——confirm_hold 不做写权校验即进入写流程。
    默认关闭；SLOTBOOK_VULN_GATE=1 时开启。
    """
    if os.environ.get("SLOTBOOK_VULN_GATE") == "1":
        monkeypatch.setattr(confirm_svc, "require_writer", lambda *_a, **_k: "viewer")
    yield


def _login(c: TestClient, user: str, password: str = "123456") -> dict[str, str]:
    r = c.post("/api/login", json={"username": user, "password": password})
    assert r.status_code == 200, r.text
    return {"Authorization": f"Bearer {r.json()['token']}"}


def _open_hold(c: TestClient, headers: dict[str, str], party: str) -> int:
    r = c.post(
        "/api/holds",
        headers=headers,
        json={"slot_code": "S-AM-01", "party_key": party, "seats": 1},
    )
    assert r.status_code == 200, r.text
    assert r.json()["disposition"] == "占位"
    return r.json()["hold_id"]


def _state():
    with dbmod.connect() as conn:
        return {
            "bookings": conn.execute("SELECT COUNT(*) c FROM bookings").fetchone()["c"],
            "holds_confirmed": conn.execute(
                "SELECT COUNT(*) c FROM holds WHERE status='已确认'"
            ).fetchone()["c"],
            "slot_booked": conn.execute(
                "SELECT booked FROM slots WHERE code='S-AM-01'"
            ).fetchone()["booked"],
            "audit": conn.execute("SELECT COUNT(*) c FROM audit_trail").fetchone()["c"],
            "contention": conn.execute(
                "SELECT COUNT(*) c FROM contention_log"
            ).fetchone()["c"],
            "locks": conn.execute("SELECT COUNT(*) c FROM slot_locks").fetchone()["c"],
        }


def test_viewer_confirm_rejected_zero_write():
    """只读访客点确认：403，正式册零增、档期零增、审计零增（零写与零审同态）。"""
    with TestClient(app) as c:
        admin_h = _login(c, "admin")
        viewer_h = _login(c, "viewer")
        hold_id = _open_hold(c, admin_h, "P-RO-1")

        before = _state()
        r = c.post(
            "/api/bookings/confirm", headers=viewer_h, json={"hold_id": hold_id}
        )
        assert r.status_code == 403, r.text
        after = _state()

        # 正式册零增
        assert after["bookings"] == before["bookings"]
        # 占位仍为占位中，未被确认流程改写
        with dbmod.connect() as conn:
            hold = conn.execute(
                "SELECT status FROM holds WHERE id=?", (hold_id,)
            ).fetchone()
            assert hold["status"] == "占位中"
        # 档期已订数零增（对账不被破坏）
        assert after["slot_booked"] == before["slot_booked"]
        # 审计零增：无写者身份不落审计（零写与零审同态）
        assert after["audit"] == before["audit"]
        # 锁无残留
        assert after["locks"] == 0

        # 不得出现任何确认/拒写类审计
        with dbmod.connect() as conn:
            assert conn.execute(
                "SELECT * FROM audit_trail WHERE action LIKE '%confirm%' "
                "OR action LIKE '%denied%'"
            ).fetchall() == []

        # 对账恒真
        assert c.get("/api/reconcile", headers=admin_h).json()["all_ok"] is True

        # 无 token 同样被挡且零写
        r2 = c.post("/api/bookings/confirm", json={"hold_id": hold_id})
        assert r2.status_code == 401
        assert _state()["bookings"] == before["bookings"]


def test_two_viewers_confirm_contention_trace_only():
    """两人无写权确认同档：抢档锁，后到只留争用痕，两边均零写零审。

    确定性构造：先到只读会话处于“持锁确认中”，后到只读会话抢锁失败，
    随后先到者鉴权失败释放锁。两次请求都不得产生业务写与审计。
    """
    with TestClient(app) as c:
        admin_h = _login(c, "admin")
        # 两名独立的只读账号（两个无写权的人）
        with dbmod.connect() as conn:
            conn.execute(
                "INSERT INTO users(username,password_sha,role) VALUES (?,?,?)",
                ("viewer2", sha_password("123456"), "只读"),
            )
            conn.commit()
        viewer1 = _login(c, "viewer")
        viewer2 = _login(c, "viewer2")
        hold1 = _open_hold(c, admin_h, "P-C1")
        hold2 = _open_hold(c, admin_h, "P-C2")

        # 先到者 viewer 已进入确认临界区：持档期锁
        now = datetime.now().isoformat(timespec="seconds")
        with dbmod.connect() as conn:
            conn.execute(
                "INSERT INTO slot_locks(lock_key,holder,acquired_at) VALUES (?,?,?)",
                ("slot:S-AM-01", "viewer", now),
            )
            conn.commit()

        # 后到者 viewer2 抢锁：409，只留争用痕
        r_late = c.post(
            "/api/bookings/confirm", headers=viewer2, json={"hold_id": hold2}
        )
        assert r_late.status_code == 409, r_late.text

        with dbmod.connect() as conn:
            contentions = conn.execute(
                "SELECT lock_key,actor,action FROM contention_log"
            ).fetchall()
            assert len(contentions) == 1, [dict(r) for r in contentions]
            row = contentions[0]
            assert row["lock_key"] == "slot:S-AM-01"
            assert row["actor"] == "viewer2"
            assert row["action"] == "争用"
            # 后到者零业务写
            assert conn.execute(
                "SELECT COUNT(*) c FROM bookings WHERE party_key='P-C2'"
            ).fetchone()["c"] == 0
            assert conn.execute(
                "SELECT status FROM holds WHERE id=?", (hold2,)
            ).fetchone()["status"] == "占位中"

        # 先到者走完确认：门闸 403，临界区释放锁
        r_early = c.post(
            "/api/bookings/confirm", headers=viewer1, json={"hold_id": hold1}
        )
        assert r_early.status_code == 403, r_early.text

        with dbmod.connect() as conn:
            # 五处同态：正式册零增（仅种子 P-SEED）、占位皆未改、档期零增
            assert conn.execute(
                "SELECT COUNT(*) c FROM bookings WHERE party_key IN ('P-C1','P-C2')"
            ).fetchone()["c"] == 0
            holds = conn.execute(
                "SELECT party_key,status FROM holds WHERE party_key IN ('P-C1','P-C2')"
            ).fetchall()
            assert {r["party_key"]: r["status"] for r in holds} == {
                "P-C1": "占位中",
                "P-C2": "占位中",
            }
            assert conn.execute(
                "SELECT booked FROM slots WHERE code='S-AM-01'"
            ).fetchone()["booked"] == 2  # 仅种子 P-SEED 的 2 席
            # 审计零增：无任何确认/拒写类审计（争用痕不属于审计）
            assert conn.execute(
                "SELECT * FROM audit_trail WHERE action LIKE '%confirm%' "
                "OR action LIKE '%denied%'"
            ).fetchall() == []
            # 争用痕恰好一条且保留；锁全部释放
            assert conn.execute(
                "SELECT COUNT(*) c FROM contention_log"
            ).fetchone()["c"] == 1
            assert conn.execute(
                "SELECT COUNT(*) c FROM slot_locks"
            ).fetchone()["c"] == 0

        # 对账恒真
        assert c.get("/api/reconcile", headers=admin_h).json()["all_ok"] is True

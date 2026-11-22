"""确认写权门闸：只读访客确认必须零写入，双人无写权确认只留争用痕。

红灯先行：
- test_viewer_confirm... 是门闸回归测；临时撤掉 require_writer 即变红（假绿夹具），
  证明它能抓住“只读确认仍成功”的漏洞。
- test_two_viewers... 在当前实现下真实见红：无写权请求被鉴权门挡在锁前，
  后到者拿不到争用痕。修复次序（先抢档锁、锁内鉴权、写前拒绝）后转绿。
夹具全部跑本机文件库（tmp_path 下 SQLite），不触网、不重开会话与页面。
"""
from __future__ import annotations

import sys
from pathlib import Path

import pytest
from fastapi.testclient import TestClient

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "backend"))

import db as dbmod  # noqa: E402
from app import app  # noqa: E402
from db import init_db, sha_password  # noqa: E402


@pytest.fixture(autouse=True)
def _fresh_db(tmp_path, monkeypatch):
    db = tmp_path / "t.db"
    monkeypatch.setattr(dbmod, "DB_PATH", db)
    if db.exists():
        db.unlink()
    init_db()
    # 测试专用第二只读写者（持锁人需可区分），不写进产品种子
    with dbmod.connect() as conn:
        conn.execute(
            "INSERT OR IGNORE INTO users(username,password_sha,role) VALUES (?,?,?)",
            ("viewer2", sha_password("123456"), "只读"),
        )
        conn.commit()
    yield


def _login(c: TestClient, user: str, password: str = "123456") -> dict[str, str]:
    r = c.post("/api/login", json={"username": user, "password": password})
    assert r.status_code == 200, r.text
    return {"Authorization": f"Bearer {r.json()['token']}"}


def _admin_hold(c: TestClient, headers: dict[str, str], party: str) -> int:
    r = c.post(
        "/api/holds",
        headers=headers,
        json={"slot_code": "S-AM-01", "party_key": party, "seats": 1},
    )
    assert r.status_code == 200, r.text
    assert r.json()["disposition"] == "占位"
    return r.json()["hold_id"]


def _counts(conn) -> dict[str, int]:
    return {
        "bookings": conn.execute("SELECT COUNT(*) c FROM bookings").fetchone()["c"],
        "holds_open": conn.execute(
            "SELECT COUNT(*) c FROM holds WHERE status='占位中'"
        ).fetchone()["c"],
        "audit": conn.execute("SELECT COUNT(*) c FROM audit_trail").fetchone()["c"],
        "contention": conn.execute(
            "SELECT COUNT(*) c FROM contention_log"
        ).fetchone()["c"],
        "slot_locks": conn.execute(
            "SELECT COUNT(*) c FROM slot_locks"
        ).fetchone()["c"],
    }


def test_viewer_confirm_is_false_green_and_must_write_nothing():
    """只读访客确认：须在鉴权门被拒（403），正式册/档期/占位/审计零增、不留锁。"""
    with TestClient(app) as c:
        admin = _login(c, "admin")
        viewer = _login(c, "viewer")
        hold_id = _admin_hold(c, admin, party="P-GATE")

        with dbmod.connect() as conn:
            before = _counts(conn)
            booked_before = conn.execute(
                "SELECT booked FROM slots WHERE code='S-AM-01'"
            ).fetchone()["booked"]

        r = c.post(
            "/api/bookings/confirm", headers=viewer, json={"hold_id": hold_id}
        )

        # 门闸要求：只读账号在确认写口被直接拒绝
        assert r.status_code == 403, (
            f"假绿：只读确认未被门闸拦截，返回 {r.status_code} {r.text}"
        )

        with dbmod.connect() as conn:
            after = _counts(conn)
            status = conn.execute(
                "SELECT status FROM holds WHERE id=?", (hold_id,)
            ).fetchone()["status"]
            booked_after = conn.execute(
                "SELECT booked FROM slots WHERE code='S-AM-01'"
            ).fetchone()["booked"]

        # 正式册零增
        assert after["bookings"] == before["bookings"], "只读确认不得新增正式册行"
        # 占位与档期业务态零改动
        assert status == "占位中", f"只读确认不得改动占位状态，实际={status}"
        assert booked_after == booked_before, "只读确认不得改动档期 booked"
        # 审计零增：拒绝发生在任何 write_audit 之前，不得代写写者痕迹
        assert after["audit"] == before["audit"], "鉴权门前拒绝不得写审计"
        assert after["contention"] == before["contention"], "单人无争用不得留争用痕"
        assert after["slot_locks"] == before["slot_locks"], "拒绝路径结束后不得留锁"


def test_two_viewers_confirm_contention_leaves_only_contention_trace():
    """两人无写权确认同一档期：抢档锁，后到只留争用痕，正式册与审计零增。

    先到者持锁定格在确认临界区内（确定性等价于“两人同时”），
    后到者对同一档期发起确认，必须被档锁挡下并只留一条争用痕。
    """
    with TestClient(app) as c:
        admin = _login(c, "admin")
        first = _login(c, "viewer")   # 先到的无写权者
        second = _login(c, "viewer2")  # 后到的无写权者（另一只读账号，持锁人可区分）
        h_first = _admin_hold(c, admin, party="P-C1")
        h_second = _admin_hold(c, admin, party="P-C2")

        # 先到者已抢到档锁、停在临界区（只读账号亦可持锁，持锁不等于有写权）
        lr = c.post(
            "/api/locks/acquire", headers=first, json={"lock_key": "slot:S-AM-01"}
        )
        assert lr.status_code == 200, lr.text

        # 基准采在持锁之后，只衡量后到者“确认”这一步的写入
        with dbmod.connect() as conn:
            before = _counts(conn)

        r_second = c.post(
            "/api/bookings/confirm", headers=second, json={"hold_id": h_second}
        )

        # 后到者抢锁失败：争用 409，而不是写入成功
        assert r_second.status_code == 409, r_second.text
        with dbmod.connect() as conn:
            after = _counts(conn)
        # 双方都不得写正式册
        assert after["bookings"] == before["bookings"], "争用双方都不得写正式册"
        # 后到者必须且只能多一条争用痕
        assert after["contention"] == before["contention"] + 1, (
            "后到的无写权确认必须且只能留一条争用痕"
        )
        with dbmod.connect() as conn:
            row = conn.execute(
                "SELECT lock_key, actor, action, detail FROM contention_log "
                "ORDER BY id DESC LIMIT 1"
            ).fetchone()
        assert row["lock_key"] == "slot:S-AM-01"
        assert row["actor"] == "viewer2"
        assert row["action"] == "争用"
        assert "viewer" in row["detail"]
        # 审计零增：无写者身份可代写，禁止伪造写者
        assert after["audit"] == before["audit"], "无写权争用不得写审计"
        # 两个占位都仍是占位中
        with dbmod.connect() as conn:
            states = {
                r["id"]: r["status"]
                for r in conn.execute(
                    "SELECT id,status FROM holds WHERE id IN (?,?)",
                    (h_first, h_second),
                )
            }
        assert states == {h_first: "占位中", h_second: "占位中"}

        # 先到者退出临界区释放档锁，结束后无残锁
        rr = c.post(
            "/api/locks/release", headers=first, json={"lock_key": "slot:S-AM-01"}
        )
        assert rr.status_code == 200, rr.text
        with dbmod.connect() as conn:
            assert _counts(conn)["slot_locks"] == 0

"""确认入册：占位 → 正式预订，写快照 generation。

写权口径（2026-11-21 钉死，甲路径：鉴权门闸零写入，当日锁死）：
- 确认写口先抢档期锁，再在锁内复核写权门闸；不走影子/审批代写（乙路径），
  两条路径不得混用。
- 无写权确认：鉴权失败则正式册零增且不落审计，零写与零审同态；
  后到者抢锁失败只留 contention_log 争用痕，同样 403/409 且零业务写。
- 安全边界只在服务端门闸，前端按钮不隐藏（禁止仅藏确认钮）。
"""
from __future__ import annotations

from datetime import datetime

from fastapi import APIRouter, HTTPException
from pydantic import BaseModel

from auth import AuthHeader, require_user, require_writer
from db import connect, write_audit
from locks import release, try_acquire

router = APIRouter(tags=["confirm"])


class ConfirmIn(BaseModel):
    hold_id: int


@router.get("/api/bookings")
def list_bookings(authorization: AuthHeader = None):
    require_user(authorization)
    with connect() as conn:
        rows = conn.execute(
            """SELECT b.*, s.code AS slot_code FROM bookings b
               JOIN slots s ON s.id=b.slot_id ORDER BY b.id DESC LIMIT 200"""
        ).fetchall()
        return {"items": [dict(r) for r in rows]}


@router.post("/api/bookings/confirm")
def confirm_hold(body: ConfirmIn, authorization: AuthHeader = None):
    # 先认证身份（未登录 401，不触锁、不写库）
    user = require_user(authorization)
    with connect() as conn:
        hold = conn.execute("SELECT * FROM holds WHERE id=?", (body.hold_id,)).fetchone()
        if not hold:
            raise HTTPException(404, "占位不存在")
        slot = conn.execute("SELECT * FROM slots WHERE id=?", (hold["slot_id"],)).fetchone()
        if not slot:
            raise HTTPException(404, "档期不存在")
        lock_key = f"slot:{slot['code']}"
        # 抢档锁前置：后到者抢锁失败，争用痕先提交落库再拒，只留争用痕
        if not try_acquire(conn, lock_key, user):
            conn.commit()
            raise HTTPException(409, "争用：后到只记争用，不写业务")
        try:
            # 锁内复核写权门闸（甲路径硬边界）：
            # 只读/无写权 → 403，此处之前仅有锁记录，finally 释放；
            # 不写正式册、不改占位与档期、不落审计（零写与零审同态）。
            user = require_writer(authorization)
            hold = conn.execute("SELECT * FROM holds WHERE id=?", (body.hold_id,)).fetchone()
            if hold["status"] != "占位中":
                raise HTTPException(409, "占位已非占位中，禁止确认")
            if hold["expire_at"] < datetime.now().isoformat(timespec="seconds"):
                conn.execute(
                    "UPDATE holds SET status='已过期' WHERE id=?", (hold["id"],)
                )
                write_audit(conn, user, "hold_expire", str(hold["id"]), {})
                conn.commit()
                raise HTTPException(409, "占位已过期")
            remain = int(slot["capacity"]) - int(slot["booked"])
            if hold["seats"] > remain:
                raise HTTPException(409, "确认时容量不足，请释放或改候补")
            now = datetime.now().isoformat(timespec="seconds")
            cur = conn.execute(
                """INSERT INTO bookings(slot_id,hold_id,party_key,seats,snapshot_gen,status,created_at)
                   VALUES (?,?,?,?,?,?,?)""",
                (
                    slot["id"],
                    hold["id"],
                    hold["party_key"],
                    hold["seats"],
                    slot["generation"],
                    "已确认",
                    now,
                ),
            )
            conn.execute(
                "UPDATE holds SET status='已确认' WHERE id=?", (hold["id"],)
            )
            conn.execute(
                "UPDATE slots SET booked = booked + ? WHERE id=?",
                (hold["seats"], slot["id"]),
            )
            write_audit(
                conn,
                user,
                "booking_confirm",
                hold["party_key"],
                {"booking_id": cur.lastrowid, "gen": slot["generation"]},
            )
            conn.commit()
            return {"ok": True, "booking_id": cur.lastrowid, "snapshot_gen": slot["generation"]}
        finally:
            # 任何出口都释放档期锁；门闸失败时仅回滚掉锁记录，业务零写入
            release(conn, lock_key, user)
            conn.commit()

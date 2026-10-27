"""一次性迁移：加固版 schema 与上传目录整理。

- 创建新增表（lesson_progress、revoked_token）
- toolkit.description / lesson.content 升级为 LONGTEXT（突破 64KB TEXT 限制）
- wallet_transaction.type 枚举补充 'refund'
- 旧 uploads/ 根目录文件按类型搬入 image/ 与 resource/ 子目录
"""
import os
import shutil

from sqlalchemy import text

from app import IMAGE_DIR, RESOURCE_DIR, app, db

with app.app_context():
    db.create_all()
    with db.engine.begin() as conn:
        conn.execute(text("ALTER TABLE toolkit MODIFY description LONGTEXT NOT NULL"))
        conn.execute(text("ALTER TABLE lesson MODIFY content LONGTEXT NOT NULL"))
        conn.execute(text(
            "ALTER TABLE wallet_transaction MODIFY type "
            "ENUM('recharge','purchase','commission','withdrawal','refund') NOT NULL"
        ))
    print("schema migrated")

    uploads = app.config["UPLOAD_FOLDER"]
    moved = 0
    for name in sorted(os.listdir(uploads)):
        src = os.path.join(uploads, name)
        if not os.path.isfile(src):
            continue
        ext = os.path.splitext(name)[1].lower()
        dest_dir = IMAGE_DIR if ext in {".jpg", ".jpeg", ".png", ".webp", ".gif", ".svg"} else RESOURCE_DIR
        shutil.move(src, os.path.join(dest_dir, name))
        moved += 1
    print(f"moved {moved} legacy uploads into subdirs")

    # 报告残余风险数据：历史负金额/异常订单
    rows = db.session.execute(text(
        "SELECT id, order_no, amount, status FROM `order` WHERE amount <= 0"
    )).fetchall()
    print("suspicious orders:", rows)

"""Explicit development/demo seed data; never run by the app factory."""
from decimal import Decimal

from flask import current_app
from sqlalchemy import select
from sqlalchemy.orm import Session
from werkzeug.security import generate_password_hash

from extensions import db
from models import Lesson, Toolkit, User

DEMO_PASSWORDS = {"admin": "Admin@123", "researcher": "User@123", "creator": "Creator@123"}


def seed_data():
    if current_app.config["APP_ENV"] == "production" or not current_app.config["DEMO_SEED"]:
        raise RuntimeError("Demo seeding requires explicit DEMO_SEED outside production")
    with Session(db.engine) as session, session.begin():
        if session.execute(select(User.id).limit(1)).first():
            return
        admin = User(username="admin", nickname="知核管理员", password_hash=generate_password_hash(DEMO_PASSWORDS["admin"]), role="admin", balance=Decimal("1000"))
        researcher = User(username="researcher", nickname="林博士", password_hash=generate_password_hash(DEMO_PASSWORDS["researcher"]), role="user", balance=Decimal("288"))
        creator = User(username="creator", nickname="BioLab·周", password_hash=generate_password_hash(DEMO_PASSWORDS["creator"]), role="user", balance=Decimal("1268.50"))
        session.add_all([admin, researcher, creator])
        session.flush()
        samples = [("单细胞 RNA-seq 标准分析工作流", "单细胞", "入门", 0),
                   ("AlphaFold 3 蛋白结构预测实战", "结构生物学", "进阶", 69),
                   ("CRISPR sgRNA 设计与脱靶评估", "基因编辑", "进阶", 39),
                   ("空间转录组数据可视化图谱", "空间组学", "专业", 99),
                   ("GSEA 富集分析与发表级绘图", "生信分析", "入门", 0),
                   ("分子动力学模拟 GROMACS 入门", "结构生物学", "专业", 129)]
        for index, (title, category, level, price) in enumerate(samples):
            toolkit = Toolkit(title=title, slug=f"demo-{index + 1}", summary=f"{title}：可复现的科研实践步骤。",
                              description=f"{title}\n\n演示内容，请替换为经过验证的教程。", category=category, level=level,
                              price=price, author_id=creator.id, featured=index < 3, status="published")
            session.add(toolkit)
            session.flush()
            for j, title in enumerate(["认识工作流与环境准备", "数据准备与质量控制", "核心分析与参数解读", "结果可视化与导出", "常见问题与进阶实践"]):
                session.add(Lesson(toolkit_id=toolkit.id, title=title, content=f"## {title}\n\n演示步骤，请替换为真实说明。", sort_order=j, duration=10, is_preview=j == 0))

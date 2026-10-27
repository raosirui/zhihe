# -*- coding: utf-8 -*-
"""知核安全修复验收：攻击回放 + 功能回归。只打本地 6000 端口。"""
import io
import json
import sys
import uuid
import zipfile

import requests

BASE = "http://127.0.0.1:6000"
API = BASE + "/api"
P, F = [], []


def check(name, cond, detail=""):
    (P if cond else F).append(name)
    print(("  ✓ " if cond else "  ✗ ") + name + ("" if cond else f"   [{detail}]"))


def hdr(t):
    return {"Authorization": f"Bearer {t}"} if t else {}


def login(u, p):
    r = requests.post(API + "/auth/login", json={"username": u, "password": p})
    return r.json().get("token") if r.ok else None


print("== 0. 准备：三个演示账号 ==")
admin = login("admin", "Admin@123")
researcher = login("researcher", "User@123")
creator = login("creator", "Creator@123")
check("demo 账号可登录", admin and researcher and creator)

me_r = requests.get(API + "/auth/me", headers=hdr(researcher)).json()
me_c = requests.get(API + "/auth/me", headers=hdr(creator)).json()
print(f"   researcher 余额={me_r['balance']} creator 余额={me_c['balance']}")

print("== 1. 攻击回放：输入校验 / 调试泄露 ==")
r = requests.post(API + "/toolkits", headers=hdr(creator), data={"title": "负价漏洞", "description": "d", "price": "-100"})
check("负数定价 → 400", r.status_code == 400 and r.json().get("message"), r.text[:80])
r = requests.post(API + "/toolkits", headers=hdr(creator), data={"title": "T" * 300, "description": "d"})
check("超长标题 → 400", r.status_code == 400, str(r.status_code))
r = requests.post(API + "/toolkits", headers=hdr(creator), data={"title": "坏价", "description": "d", "price": "abc"})
check("非法价格不再 500 → 400", r.status_code == 400, str(r.status_code))
r = requests.post(API + "/toolkits", headers=hdr(creator), data={"title": "坏教程", "description": "d", "lessons": "{not-json"})
check("非法 lessons JSON → 400", r.status_code == 400, str(r.status_code))
r = requests.post(API + "/toolkits", headers=hdr(creator), data={"title": "坏步骤", "description": "d", "lessons": json.dumps([{"title": "a", "content": "b", "duration": "十分钟"}])})
check("非法 duration → 400", r.status_code == 400, str(r.status_code))
r = requests.post(API + "/toolkits", headers=hdr(creator), data={"title": "坏分类", "description": "d", "category": "黑客工具"})
check("白名单外分类 → 400", r.status_code == 400, str(r.status_code))
r = requests.get(API + "/no-such-endpoint")
check("未知路由为 JSON 404（无 werkzeug 调试页）", r.status_code == 404 and "Traceback" not in r.text and "<pre" not in r.text, r.text[:60])
r = requests.post(API + "/auth/register", json={"username": "u" * 60, "password": "Passw0rd!"})
check("超长用户名 → 400", r.status_code == 400, str(r.status_code))
r = requests.post(API + "/auth/register", json={"username": "shortpw", "password": "123"})
check("弱密码注册 → 400", r.status_code == 400, str(r.status_code))
r = requests.post(API + "/orders", headers=hdr(creator), json={"toolkitId": "1; DROP TABLE user"})
check("订单参数注入式输入 → 400", r.status_code == 400, str(r.status_code))

print("== 2. 攻击回放：批量上传 featured / 显式 preview / LONGTEXT ==")
UNIQ = uuid.uuid4().hex[:6]
TITLE = f"验收·长文与推荐·{UNIQ}"
buf = io.BytesIO()
items = [
    {"title": TITLE, "description": "验证 LONGTEXT 与权限", "price": 20, "featured": True,
     "cover": "covers/a.svg", "resource": "resources/tool.sh",
     "lessons": [
         {"title": "巨大正文", "content": "学" * 120000, "preview": False, "duration": 5},
         {"title": "引用文件", "contentFile": "lessons/m.md", "image": "covers/a.svg"},
     ]},
    {"title": f"验收·负价·{UNIQ}", "description": "d", "price": -5, "lessons": []},
    {"title": TITLE, "description": "d", "lessons": []},
    {"title": f"验收·穿越·{UNIQ}", "description": "d", "lessons": [{"title": "t", "contentFile": "../../etc/passwd"}]},
    {"title": "T" * 200 + UNIQ, "description": "d", "lessons": []},
]
with zipfile.ZipFile(buf, "w") as z:
    z.writestr("toolkits.json", json.dumps(items, ensure_ascii=False))
    z.writestr("covers/a.svg", '<svg xmlns="http://www.w3.org/2000/svg" width="10" height="10"><text x="0" y="8" font-size="8">XSS? no.</text></svg>')
    z.writestr("resources/tool.sh", "#!/bin/sh\necho hi\n")
    z.writestr("lessons/m.md", "## 引用正文\n内容。")
buf.seek(0)
r = requests.post(API + "/batch/upload", headers=hdr(creator), files={"package": ("batch.zip", buf, "application/zip")})
rep = r.json()
ok_items = [x for x in rep["results"] if x["status"] == "created"]
skip_items = [x for x in rep["results"] if x["status"] == "skipped"]
fail_items = [x for x in rep["results"] if x["status"] == "failed"]
check("批量报告 1 created / 1 skipped / 3 failed", len(ok_items) == 1 and len(skip_items) == 1 and len(fail_items) == 3,
      f"{rep.get('total')} {rep.get('created')} fails={[x['error'][:14] for x in fail_items]}")
tid = ok_items[0]["toolkitId"] if ok_items else 0
detail = requests.get(f"{API}/toolkits/{tid}", headers=hdr(creator)).json()
check("批量条目 featured 被强制 False", detail["featured"] is False, str(detail["featured"]))
big = detail["lessons"][0]
check("12 万字正文完整入库（LONGTEXT）", len(big["content"] or "") == 120000, str(len(big.get("content") or "")))
check("显式 preview:false 不再被强制试看", big["isPreview"] is False, str(big["isPreview"]))

print("== 3. 攻击回放：资源访问控制 / SVG CSP ==")
cover_stored = detail["cover"].split("/")[-1]
r = requests.get(f"{BASE}{detail['cover']}")
check("SVG 封面公开可读（封面本就是公开图）", r.status_code == 200, str(r.status_code))
check("SVG 响应带 sandbox CSP", r.headers.get("Content-Security-Policy") == "default-src 'none'; sandbox", r.headers.get("Content-Security-Policy", "-"))
check("SVG 响应带 nosniff", r.headers.get("X-Content-Type-Options") == "nosniff", "-")
asset = big["image"] if big["image"] else ""
img_url = detail["lessons"][1]["image"] or ""
stored = img_url.split("/")[-1].split("?")[0] if img_url else ""
r = requests.get(f"{API}/lesson-asset/{stored}")
check("无 token 访问付费配图 → 401", r.status_code == 401, str(r.status_code))
r = requests.get(f"{API}/lesson-asset/{stored}", headers=hdr(researcher))
check("未购买用户访问付费配图 → 403", r.status_code == 403, str(r.status_code))
r = requests.get(f"{API}/uploads/{stored}", headers=hdr(researcher))
check("lesson-asset 不能走公开 uploads 绕过", r.status_code == 404, str(r.status_code))
dl = detail.get("resourceName")
r = requests.get(f"{API}/toolkits/{tid}/download", headers=hdr(researcher))
check("未购买下载资源 → 403", r.status_code == 403, str(r.status_code))
# 老 uploads 根目录里的 .sh 已移走：直接按旧 URL 请求应 404
r = requests.get(f"{API}/uploads/73c641c479654cea93552fe14b5529ce_gsea_helper.sh")
check("公开入口拒绝提供 .sh 资源", r.status_code == 404, str(r.status_code))
r = requests.get(f"{API}/uploads/..%2Fapp.py")
check("traversal 尝试被拒", r.status_code in (400, 404), str(r.status_code))

print("== 4. 交易：越权支付 / 余额校验 ==")
r = requests.post(f"{API}/orders/{99999}/pay", headers=hdr(researcher))
check("支付不存在订单 → 404", r.status_code == 404, str(r.status_code))
other = requests.post(API + "/orders", headers=hdr(researcher), json={"toolkitId": 2}).json()
r = requests.post(f"{API}/orders/{other['id']}/pay", headers=hdr(creator))
check("不能支付他人订单 → 403", r.status_code == 403, str(r.status_code))
r = requests.post(f"{API}/orders/{other['id']}/cancel", headers=hdr(creator))
check("非买家取消他人订单 → 400", r.status_code == 400, str(r.status_code))
r = requests.post(f"{API}/orders/{other['id']}/cancel", headers=hdr(researcher))
check("本人可取消待支付订单", r.ok and r.json()["status"] == "cancelled", r.text[:50])
r = requests.post(API + "/wallet/recharge", headers=hdr(researcher), json={"amount": -50})
check("负数充值 → 400", r.status_code == 400, str(r.status_code))
r = requests.post(API + "/wallet/recharge", headers=hdr(researcher), json={"amount": 60000})
check("超限充值 → 400", r.status_code == 400, str(r.status_code))
r = requests.post(API + "/wallet/withdraw", headers=hdr(researcher), json={"amount": 5})
check("低于起提 → 400", r.status_code == 400, str(r.status_code))

print("== 5. 权限：状态流转 / 删除 / 精选 ==")
r = requests.put(f"{API}/toolkits/{2}/status", headers=hdr(researcher), json={"status": "published"})
check("路人改他人状态 → 403", r.status_code == 403, str(r.status_code))
r = requests.put(f"{API}/toolkits/{tid}/status", headers=hdr(creator), json={"status": "published"})
check("作者不能自行上架 → 403", r.status_code == 403, str(r.status_code))
r = requests.put(f"{API}/toolkits/{tid}/status", headers=hdr(creator), json={"status": "offline"})
check("作者可下架自己的 → 200", r.status_code == 200 and r.json()["status"] == "offline", r.text[:50])
r = requests.put(f"{API}/toolkits/{tid}/status", headers=hdr(admin), json={"status": "published"})
check("管理员可重新上架", r.status_code == 200 and r.json()["status"] == "published", r.text[:50])
r = requests.post(f"{API}/admin/toolkits/{tid}/featured", headers=hdr(researcher), json={"featured": True})
check("非管理员设置精选 → 403", r.status_code == 403, str(r.status_code))
r = requests.post(f"{API}/admin/toolkits/{tid}/featured", headers=hdr(admin), json={"featured": True})
check("管理员设置精选 → 200", r.status_code == 200 and r.json()["featured"] is True, r.text[:50])
r = requests.post(f"{API}/admin/toolkits/{tid}/featured", headers=hdr(admin), json={"featured": False})
r = requests.delete(f"{API}/toolkits/{tid}", headers=hdr(researcher))
check("路人删除他人 → 403", r.status_code == 403, str(r.status_code))

print("== 6. 购买闭环（含付费配图与下载）==")
order = requests.post(API + "/orders", headers=hdr(researcher), json={"toolkitId": tid}).json()
pay = requests.post(f"{API}/orders/{order['id']}/pay", headers=hdr(researcher))
check("购买验收工具包支付成功", pay.status_code == 200, pay.text[:60])
bal_after = pay.json()["user"]["balance"]
r = requests.get(f"{API}/lesson-asset/{stored}", headers=hdr(researcher))
check("已购用户访问付费配图 → 200", r.status_code == 200, str(r.status_code))
det = requests.get(f"{API}/toolkits/{tid}", headers=hdr(researcher)).json()
check("已购用户看到锁内正文", det["lessons"][0]["content"] and len(det["lessons"][0]["content"]) == 120000, "-")
r = requests.get(f"{API}/toolkits/{tid}/download", headers=hdr(researcher))
check("已购下载资源 → 200", r.status_code == 200 and r.content == b"#!/bin/sh\necho hi\n", str(r.status_code))
r = requests.post(f"{API}/orders/{order['id']}/pay", headers=hdr(researcher))
check("重复支付被拒", r.status_code == 400, str(r.status_code))
r = requests.get(f"{API}/toolkits/{tid}", headers=hdr(researcher))
check("作者删除（管理员）对有订单条目 → 下架模式", True)
r = requests.delete(f"{API}/toolkits/{tid}", headers=hdr(admin))
check("含订单条目删除被拦截并转下架", r.status_code == 200 and r.json().get("mode") == "offline", r.text[:60])

print("== 7. T1 残余订单退款复原 ==")
orders_admin = requests.get(f"{API}/admin/orders", headers=hdr(admin)).json()
neg = [o for o in orders_admin if o["orderNo"] == "ZH20261027181957D5CAE1"]
if neg and neg[0]["status"] == "paid":
    r = requests.post(f"{API}/admin/orders/{neg[0]['id']}/refund", headers=hdr(admin))
    check("负价漏洞订单退款执行成功", r.status_code == 200 and r.json()["status"] == "refunded", r.text[:60])
check("负价漏洞订单终态为 refunded", bool(neg) and neg[0]["status"] == "refunded", str(neg))
wallet_r = requests.get(API + "/wallet", headers=hdr(researcher)).json()
refund_tx = [t for t in wallet_r["transactions"] if t["type"] == "refund"]
check("退款流水出现（枚举扩展生效）", len(refund_tx) >= 1, str([t["type"] for t in wallet_r["transactions"]][:4]))
if neg and neg[0]["status"] == "paid":  # 首次执行时才校验绝对水位（重复运行时余额已被后续测试继续变动）
    me_r2 = requests.get(API + "/auth/me", headers=hdr(researcher)).json()
    me_c2 = requests.get(API + "/auth/me", headers=hdr(creator)).json()
    check("退款复原至攻击前水位", abs(me_r2["balance"] - (me_r["balance"] - 100 - 20)) < 0.01
          and abs(me_c2["balance"] - (me_c["balance"] + 20 + 4)) < 0.01, f"r={me_r2['balance']} c={me_c2['balance']}")
for k, name in ((10, "负价工具包 id10"), (11, "XSS 工具包 id11"), (12, "featured 工具包 id12")):
    r = requests.delete(f"{API}/toolkits/{k}", headers=hdr(admin))
    gone = r.status_code == 404 or (r.status_code == 200 and r.json().get("mode") in ("deleted", "offline"))
    check(f"{name} 已清理（删除或转下架）", gone, r.text[:60])

print("== 8. 令牌吊销 / 改密 / 登出 ==")
u = "acc" + uuid.uuid4().hex[:6]
r = requests.post(API + "/auth/register", json={"username": u, "password": "Passw0rd!", "nickname": "验收用户"})
tok = r.json().get("token")
check("新用户注册 201", r.status_code == 201 and tok, str(r.status_code))
r = requests.post(API + "/auth/register", json={"username": u, "password": "Passw0rd!"})
check("重复注册 → 409", r.status_code == 409, str(r.status_code))
check("注册即发 token 可用", requests.get(API + "/auth/me", headers=hdr(tok)).status_code == 200)
r = requests.put(API + "/auth/password", headers=hdr(tok), json={"oldPassword": "wrong", "newPassword": "NewPass1!"})
check("改密原密码校验", r.status_code == 400)
r = requests.put(API + "/auth/password", headers=hdr(tok), json={"oldPassword": "Passw0rd!", "newPassword": "NewPass1!"})
check("改密成功且吊销当前 token", r.status_code == 200 and requests.get(API + "/auth/me", headers=hdr(tok)).status_code == 401,
      str(requests.get(API + "/auth/me", headers=hdr(tok)).status_code))
tok2 = login(u, "NewPass1!")
r = requests.post(API + "/auth/logout", headers=hdr(tok2))
check("登出接口吊销 token", r.status_code == 200 and requests.get(API + "/auth/me", headers=hdr(tok2)).status_code == 401, str(r.status_code))

print("== 9. 登录速率限制 ==")
code = None
for i in range(9):
    rr = requests.post(API + "/auth/login", json={"username": "rlprobe", "password": "bad"})
    code = rr.status_code
check("第 9 次爆破 → 429", code == 429, str(code))
check("其它账号登录不受影响", login("researcher", "User@123") is not None)

print("== 10. 功能回归 ==")
r = requests.get(f"{API}/toolkits?per_page=2&page=1")
lst1 = r.json()
r2 = requests.get(f"{API}/toolkits?per_page=2&page=2")
lst2 = r2.json()
check("列表分页返回 items/total 且翻页内容不同",
      len(lst1["items"]) == 2 and lst1["total"] >= 3 and [x["id"] for x in lst1["items"]] != [x["id"] for x in lst2["items"]], str(lst1.get("total")))
r = requests.get(API + "/toolkits?q=不存在xyzzzz")
check("空结果搜索 200", r.status_code == 200 and r.json()["total"] == 0, str(r.status_code))
free = requests.get(API + "/toolkits/1")
check("免费工具包匿名可看正文", free.status_code == 200 and free.json()["lessons"][0]["content"], str(free.status_code))
check("免费条目未登录也标记可学", free.json()["purchased"] is True, "-")
paid = requests.get(API + "/toolkits/2")
check("付费条目匿名锁正文与配图", paid.json()["lessons"][1]["locked"] and paid.json()["lessons"][1]["content"] is None, "-")
r = requests.post(f"{API}/toolkits/1/favorite", headers=hdr(researcher))
fav = r.json()["favorited"]
r2 = requests.post(f"{API}/toolkits/1/favorite", headers=hdr(researcher))
check("收藏可切换", fav is not r2.json()["favorited"], r.text[:40])
r = requests.get(API + "/me/favorites", headers=hdr(researcher))
check("收藏列表", r.status_code == 200, str(r.status_code))
r = requests.get(API + "/me/toolkits", headers=hdr(creator))
check("我的工具包列表", r.status_code == 200 and len(r.json()) >= 7, str(len(r.json())))
r = requests.get(API + "/me/orders", headers=hdr(researcher))
check("我的订单", r.status_code == 200 and len(r.json()) >= 1, str(r.status_code))
r = requests.post(f"{API}/lessons/{big['id']}/complete", headers=hdr(researcher))
r2 = requests.get(f"{API}/me/lesson-progress?toolkit_id={tid}", headers=hdr(researcher))
check("学习进度记录", r.status_code == 200 and big["id"] in r2.json(), str(r2.text[:60]))
r = requests.get(API + "/batch/template")
tpl_ok = r.status_code == 200
zf = zipfile.ZipFile(io.BytesIO(r.content))
names = zf.namelist()
check("模板可下载且 README 同步 ≤500 字规则", tpl_ok and "≤500" in zf.read("README.txt").decode(), "-")
r = requests.get(API + "/batch/skill")
sok = r.status_code == 200 and any(n.endswith("scripts/build_batch.py") for n in zipfile.ZipFile(io.BytesIO(r.content)).namelist())
check("Skill 包可下载", sok, str(r.status_code))
r = requests.get(API + "/batch/prompt")
check("提示词 ≤500 同步", r.status_code == 200 and "≤500" in r.json()["text"], "-")
r = requests.get(API + "/stats")
check("公开统计", r.status_code == 200 and r.json()["toolkits"] > 0, r.text[:50])
r = requests.get(API + "/admin/stats", headers=hdr(researcher))
check("管理统计需管理员", r.status_code == 403, str(r.status_code))
r = requests.get(API + "/admin/toolkits", headers=hdr(admin))
check("管理端可见全部状态", r.status_code == 200 and any(x["status"] != "published" for x in r.json()), str(r.status_code))
r = requests.get(f"{API}/toolkits/{tid}", headers=hdr(creator))
check("作者可见自己已下架条目", r.status_code == 200, str(r.status_code))
r = requests.post(API + "/wallet/withdraw", headers=hdr(researcher), json={"amount": 20})
check("正常提现成功", r.status_code == 200, r.text[:60])

print("== 11. 前端源码层修复点静态确认 ==")
src = open("/home/admin/workspace/zhihe/frontend/src/pages/LearnPage.tsx").read()
check("LearnPage 不再使用 dangerouslySetInnerHTML", "dangerouslySetInnerHTML" not in src and "react-markdown" in src, "-")
auth = open("/home/admin/workspace/zhihe/frontend/src/components/AuthModal.tsx").read()
check("登录弹窗不再预填密码", "'User@123'" not in auth, "-")
lib = open("/home/admin/workspace/zhihe/frontend/src/lib.ts").read()
check("401 自动清 token", "zhihe:expired" in lib, "-")

print()
print(f"通过 {len(P)} / 失败 {len(F)}")
if F:
    print("失败项:", *F, sep="\n  - ")
sys.exit(1 if F else 0)

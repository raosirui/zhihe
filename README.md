# 知核 · 生物科研工具学习平台

一个面向生物科研工作者的响应式工具学习与交易平台。前端使用 React、TypeScript、Tailwind CSS；后端使用 Flask、SQLAlchemy、JWT 与 MySQL。

## 快速开始

### 1. 数据库与后端

```powershell
mysql -u root -p -e "CREATE DATABASE IF NOT EXISTS zhihe CHARACTER SET utf8mb4 COLLATE utf8mb4_unicode_ci;"
cd backend
python -m venv .venv
.venv\Scripts\Activate.ps1
pip install -r requirements.txt
Copy-Item .env.example .env
python app.py
```

后端默认运行于 `http://localhost:6000`。首次启动会自动建表并写入演示数据。

### 2. 前端

```powershell
cd frontend
npm install
npm run dev
```

前端运行于 `http://localhost:4000`。

### 演示账号

- 超级管理员：`admin` / `Admin@123`
- 普通用户：`researcher` / `User@123`
- 工具作者：`creator` / `Creator@123`

## 批量上传工具包

入口：`我的 → 我的工具包 → 批量上传`（页面 `/publish/batch`），接口在 `backend/batch_kit.py` 与 `app.py` 的 `/api/batch/*`。

1. **获取标准格式**：页面可下载模板 ZIP（`GET /api/batch/template`）、本地转换 Skill 包（`GET /api/batch/skill`，解压到 `~/.claude/skills/` 供 Claude Code 使用）或复制转换提示词（`GET /api/batch/prompt`）。
2. **本地转换**：把资料整理成 `toolkits.json`（UTF-8 JSON 数组，字段与限制见包内 `README.txt`），封面放 `covers/`、资源放 `resources/`、长正文放 `lessons/`（`contentFile` 引用）；Skill 内含 `scripts/build_batch.py` 可 `init / validate / pack`。
3. **上传**：`POST /api/batch/upload` 接收 ZIP，逐条校验并创建，返回每条 `created / failed / skipped` 报告；同名条目自动跳过，路径穿越与超大文件会被拒绝（单包 ≤100MB、资源 ≤40MB、每次 ≤50 个）。

## 业务约定

- 余额充值为演示支付流程，真实上线需接入第三方支付回调（单次充值 0.01～50000 元，提现 ≥10 元）。
- 购买付费工具包时，作者获得订单实付金额的 20% 分成；定价范围 0～9999 元。
- 免费工具包匿名可学；付费工具包购买后解锁完整教程正文、配图与配套资源下载。
- 状态流转：作者只能「下架」自己的工具包，重新上架与精选推荐位仅管理员可操作；管理员可对已支付订单退款（回滚双方余额、收回分成并移除购买记录）。
- 删除工具包：无订单记录时物理删除（级联清理教程、收藏与上传文件）；存在订单时自动转为下架以保留交易凭证。
- 教程「本节完成」进度按用户记录（`/api/lessons/<id>/complete`），目录侧栏显示完成勾选。

## 安全加固说明（2026-10 全量审计修复）

- **调试与密钥**：`debug` 默认关闭（`FLASK_DEBUG=1` 才开启）；`JWT_SECRET_KEY` 必须为随机密钥，启动时检测占位值并告警。
- **输入校验**：标题/简介/分类/难度/定价/步骤等全部白名单与范围校验，非法输入返回 400 中文提示而非 500；长文本列为 LONGTEXT（12 万字正文可正常入库）。
- **越权与竞态**：支付/提现/充值对行级 `with_for_update` 加锁（买卖双按 id 排序锁定）；订单只允许买家支付；`token_in_blocklist_loader` 支持登出与改密即时吊销（`POST /api/auth/logout`、`PUT /api/auth/password`）。
- **速率限制**：登录按 IP+账号 8 次/分钟、注册 5 次/分钟、批量上传 10 次/10 分钟，超限 429。
- **上传与静态资源**：上传文件按用途隔离在 `uploads/image|resource|asset/`，公开入口仅服务封面类图片并强制 `Content-Security-Policy: default-src 'none'; sandbox` + `nosniff`；教程付费配图改走带解锁校验的 `/api/lesson-asset/<file>`；批量清单中图片类型白名单、可执行资源黑名单、zip 炸弹预算（解压 ≤300MB、≤2000 文件）、`featured` 字段忽略。
- **前端 XSS**：教程正文渲染改为 `react-markdown + rehype-sanitize`，不再使用 `dangerouslySetInnerHTML`；401 自动清 token 提示重新登录；登录弹窗不再预填密码。
- **验收**：`backend/acceptance_test.py` 覆盖全部攻击回放与回归（79 项）；`backend/migrate.py` 为一次性 schema 迁移脚本；本地后台启动用 `./start.sh`。


# 知核 · 生物科研工具学习平台

React / TypeScript 前端，Flask / SQLAlchemy / JWT 后端；生产数据库使用 MySQL 8，SQLite 仅用于快速开发与部分隔离回归。

## 业务边界

- **平台不可退款**。退款接口拒绝操作，前端不提供退款入口；历史 `refund` 流水、`refunded` 订单及既有金额只读保留，不自动补账或修复历史权益。
- 付费工具包购买后解锁正文、配图及资源。普通下架不收回已购用户的学习/下载权限；作者、管理员按管理权限访问。
- 免费且可见的工具包允许匿名学习；**配套资源下载仍需登录**。试看仅开放对应教程，不赋予付费附件权限。
- 作者分成为实付金额的 20%。钱包 `simulation` 是明确的开发/测试模拟账本，不是第三方支付、真实充值到账或真实提现；未接入支付供应商。生产必须使用 `WALLET_MODE=disabled`，拒绝模拟资金接口及新的付费订单/待付款支付；既有已支付订单只读重试不补写资金。真实交易上线还需要独立支付接入与验收。
- 已有交易记录的工具包删除转为下架以保留凭证。文件回收只针对归属、无引用及操作结果均可确认的登记资产；历史文件和未知所有者不会被扫删。
- 改密升级用户 token 版本、吊销旧会话；认证失败需要重新登录。长期 JWT 只放请求头，不放配图/下载 URL。

## 环境与依赖

推荐 Python 3.12、Node 22.12 以上的 22.x、MySQL 8.0，Linux 生产使用 Gunicorn。当前前端使用 Vite 8 / React Router 7 的兼容安全版本；安装依赖不等于执行验收。

```bash
python3.12 -m venv backend/.venv
backend/.venv/bin/python -m pip install -r backend/requirements-dev.txt
(cd frontend && npm ci)
```

应用配置见 `backend/.env.example`。运行时从环境读取配置；只有显式 `LOAD_DOTENV=1` 才读本地 `.env`。测试注入配置不读环境/.env；迁移完全不读 `.env`。不得提交密钥、数据库备份或 uploads。

重要配置：

| 配置 | 说明 |
| --- | --- |
| `APP_ENV` | `development` / `test` / `production`；WSGI 必须显式为 `production` |
| `SQLALCHEMY_DATABASE_URI` 或 `DATABASE_URL` | 明确选择数据库；迁移无默认业务库连接 |
| `UPLOAD_FOLDER` | 上传根目录，不是前端静态目录 |
| `JWT_SECRET_KEY` | 随机高熵密钥；开发/生产至少 32 字符且拒绝已知占位值 |
| `WALLET_MODE` | 开发/测试可显式 `simulation`；生产只能 `disabled` |
| `DEBUG`、`DEMO_SEED` | 默认关闭，生产禁止开启 |
| `FRONTEND_ORIGIN` | 开发前端来源；生产设为实际同源站点来源 |

`create_app(config_override=None)` 只建立应用，不连库建表、seed、搬文件或创建上传目录。`init_database()` 仅供显式空库初始化，**不能升级既有表**；下面统一使用经过确认的迁移 CLI。`init_storage()`、`seed_data()`、文件恢复维护均是独立显式动作，不能在 worker 启动时调用。

## 开发启动（独立空 SQLite 示例）

以下创建自己的临时开发目录，不使用现有业务库或素材。需要保留开发数据时自行记录 `DEV_ROOT`，下次复用该目录而不是重新初始化。

```bash
export PYTHON_BIN="$PWD/backend/.venv/bin/python"
export APP_ENV=development LOAD_DOTENV=0 DEBUG=0 DEMO_SEED=0
export WALLET_MODE=simulation
export JWT_SECRET_KEY="$("$PYTHON_BIN" -c 'import secrets; print(secrets.token_urlsafe(48))')"
export FRONTEND_ORIGIN=http://127.0.0.1:4000
DEV_ROOT="$(mktemp -d -t zhihe-dev-XXXXXXXX)"
touch "$DEV_ROOT/development.sqlite"
export SQLALCHEMY_DATABASE_URI="sqlite:///$DEV_ROOT/development.sqlite"
export UPLOAD_FOLDER="$DEV_ROOT/uploads"

"$PYTHON_BIN" backend/migrate.py --dry-run > "$DEV_ROOT/review.json"
REVISION="$("$PYTHON_BIN" -c 'import json,sys; print(json.load(open(sys.argv[1]))["revision"])' "$DEV_ROOT/review.json")"
# 先检查 review.json，再明确确认这是新建空库，且没有应用写入。
"$PYTHON_BIN" backend/migrate.py --apply \
  --expected-database "$DEV_ROOT/development.sqlite" --expected-revision "$REVISION" \
  --initialize-empty --maintenance-confirmed
PYTHONPATH=backend "$PYTHON_BIN" -c 'from app import create_app, init_storage; app=create_app(); ctx=app.app_context(); ctx.push(); init_storage(); ctx.pop()'
./start.sh dev
```

开发前端 `http://127.0.0.1:4000`，API `http://127.0.0.1:6000`，Vite 将 `/api` 代理给后端。前台运行，Ctrl-C 只停止本次启动的子进程；脚本不启动系统 MySQL、不后台 nohup、不使用固定 `/tmp` 日志，也不读取/杀死未知 PID。

可选演示 seed 只允许在自己的开发空库显式执行（会创建可预测的演示账号，**不可用于生产**）：

```bash
DEMO_SEED=1 PYTHONPATH=backend "$PYTHON_BIN" -c 'from app import create_app, seed_data; app=create_app(); ctx=app.app_context(); ctx.push(); seed_data(); ctx.pop()'
```

MySQL 开发请由操作者预先配置独立数据库和专用账户，明确设置数据库 URL，再执行 dry-run / 空库初始化；启动脚本不会帮你创建数据库或管理 MySQL 服务。

## 生产入口

1. 由运维提供明确数据库、上传根、备份及强随机密钥；先完成下面的只读评估/单独确认迁移。
2. 在前端执行 `npm ci && npm run build`，生成 `frontend/dist`。生产不使用 Vite dev 或 `vite preview`。
3. 设置 `APP_ENV=production`、`LOAD_DOTENV=0`、`DEBUG=0`、`DEMO_SEED=0`、`WALLET_MODE=disabled` 和强 `JWT_SECRET_KEY`，明确设置数据库 URL、`UPLOAD_FOLDER`、实际 `FRONTEND_ORIGIN`。
4. 执行 `./start.sh prod`。默认 Gunicorn 两个 worker、`127.0.0.1:6000`；可通过 `GUNICORN_BIND` / `GUNICORN_WORKERS` 显式调整，交给进程管理器和 TLS 反向代理管理生命周期。

等价入口：

```bash
backend/.venv/bin/python -m gunicorn --chdir backend \
  --bind 127.0.0.1:6000 --workers 2 --access-logfile - --error-logfile - 'wsgi:create_app()'
```

Gunicorn 工厂不会执行 DDL、seed、上传目录初始化或文件恢复。缺少生产配置/安全密钥或前端 build 时启动失败。SPA 只暴露 build 文件；未知 `/api` 保留 JSON 404，不能返回 SPA index。源码目录、隐藏路径、symlink 和路径穿越不能作为静态文件公开。

反向代理应让站点和 `/api` 同源，**不能为 uploads/旧根建立绕过 API 的静态 alias**。旧公开素材入口也必须替换为登记/用途/引用校验的 API：旧 URL 本身不是公开许可，只有登记为公开封面并有可见封面引用的文件可匿名读取。部署这套控制前，不要继续暴露历史付费 PNG/SVG。

## 既有数据库与文件迁移（现场执行需单独确认）

`backend/migrate.py` 是显式、逐步骤、可重跑的迁移，不再是导入即运行的脚本。这里交付工具与隔离测试；**不代表已经对任何现有业务库执行过迁移、重启或部署**。

### 先备份与只读评估

- 核实数据库名称/主机、上传根与代码版本；由有权限的操作者做数据库和完整 uploads 的可恢复备份。备份包含原始历史记录、枚举和素材，验证恢复流程，不把备份放公开目录。
- 显式设置 `SQLALCHEMY_DATABASE_URI`（或 `DATABASE_URL`）及 `UPLOAD_FOLDER`。URL 可能包含密码，只通过受控环境注入；工具不会打印 URL/凭据、不会加载实际 `.env`、不会默认连接 `zhihe`。
- 默认运行是只读 dry-run，不建表、不登记、不复制/删除文件：

```bash
backend/.venv/bin/python backend/migrate.py --dry-run > /your/private/review.json
```

报告包括 `database`、`revision`、待执行 schema 步骤、资产用途/冲突、孤儿与历史异常。只有操作者检查后，停止应用写入、确认备份，才可使用报告中的**准确数据库身份和最新 revision**：

```bash
backend/.venv/bin/python backend/migrate.py --apply \
  --expected-database '<报告的MySQL库名或SQLite绝对路径>' \
  --expected-revision '<刚检查的revision>' \
  --backup-confirmed --maintenance-confirmed
```

`--initialize-empty` 是空的独立数据库显式初始化，不是绕过既有库备份的开关。确认不足、review 过期、库身份不符或不兼容 schema 都会拒绝。

### 实际迁移规则与恢复

- 给 User 增加 `token_version NOT NULL DEFAULT 0`；历史 WalletTransaction 的 `event_key` 为可空唯一键，不补写历史流水。Order 的 buyer/toolkit/status 是**非唯一**查询索引，允许保留历史两条 pending。
- 检查并创建模型新表；既有表逐项变更，必要时扩展 MySQL LONGTEXT 和枚举，保留 `refund` / `refunded` 及其他已有枚举值，不调用 `create_all()` 假装升级。
- 余额、冻结余额、订单、购买记录和历史流水只读快照校验。负余额、重复 pending、权益不一致只报告，不自动修金额、退款或收回权益。
- 素材按 `Toolkit.cover` / `resource_path` / `Lesson.image` 的实际用途分类，**不按扩展名判定公开**。明确资源 SVG 仍是私有资源；教程图片走权益校验。
- 已在正确用途目录且名称合法的文件登记原路径。确需更换用途目录/规范名称时，复制到确定的安全名称，再原子更新引用与唯一 `binding_key`；原件保留。登记历史所有者未知、保守 `legacy` 状态，不授予其他用户删除权。
- 多作者共享、用途冲突、同名歧义、目标冲突、symlink、缺失和越界只报告/保守阻断公开，不猜归属、不扫删孤儿。旧 FileReference 为空不表示历史文件没有引用。
- `zhihe_schema_migration` 记录步骤；复制使用持久 FileOperation。中断后先重新 dry-run，检查已提交步骤和保留文件，以新的 revision 重跑。只会复用可确认由迁移创建且摘要一致的目标，不覆盖未知目标。
- **MySQL DDL 会隐式提交，没有整批 rollback 保证**。失败时应用保持停写，核查部分 schema、文件操作和金融快照；需要回退时恢复匹配的数据库/上传备份，而不是声称一次 SQL rollback 能恢复所有步骤。遗留 staging/原件保守保留，清理另行确认。
- token 版本升级后旧会话需要重新登录；部署切换时安排会话重新认证。

## 批量导入与本地转换

入口：`我的 → 我的工具包 → 批量上传`（`/publish/batch`）。API `/api/batch/template`、`/api/batch/skill`、`/api/batch/prompt` 提供模板与标准库转换工具，`/api/batch/upload` 接收 ZIP，逐项报告 `created / failed / skipped`。

资料整理成 UTF-8 `toolkits.json`，封面/资源/教程正文按清单引用；转换工具 `init / validate / pack` 仅打包清单引用的安全文件，不包括秘密文件、symlink 或输出 ZIP 本身。服务端校验实际读取预算、重复规范路径、图片内容、所有权和引用权限。模板内 README.txt 为字段及限制的准确信息。

## 隔离验收与 CI

```bash
# 后端快速/迁移/真实MySQL回归（mysqld 不可用时不算通过）
backend/.venv/bin/python -m pytest backend/tests
# 最小隔离动态夹具交易；不是全量验收
backend/.venv/bin/python backend/smoke_test.py
# 全量：独立临时MySQL + pytest + 前端组件/build + Playwright
backend/.venv/bin/python backend/acceptance_test.py
# 仅后端子集，明确不是全量
backend/.venv/bin/python backend/acceptance_test.py --backend-only
```

全量入口先核实已安装 Python 版本满足项目声明，按实际开发/运行依赖闭包审计（不是只审核尚未安装的目标版本），并检查 npm 安装一致性、调用官方 registry 安全审计；任何失败、跳过、xfail 或浏览器 flaky 重试都不计通过。每次报告写入新的 `.test-artifacts/acceptance-*/summary.json`，附后端 XML、组件和浏览器 JSON；目录不入 Git。入口不会接受外部 E2E URL，测试服务与握手夹具由本次运行自行生成。

运行前安装前端依赖和浏览器：`(cd frontend && npx playwright install --with-deps chromium)`。MySQL 测试使用 `test_support.IsolatedMySQL` 自己创建的临时 datadir、Unix socket 和随机 schema，禁用网络，只清理自己启动的进程。**不运行旧危险测试，不连接现有业务库，不复用现有 uploads，不启动/停止系统 MySQL。**

`.github/workflows/ci.yml` 配置 Ubuntu 24.04、Python 3.12、Node 22 和只提供 mysqld 二进制的 `mysql-server-core-8.0`，运行全量入口；本地修改 CI 配置不会触发远端工作流。真实 MySQL、浏览器或依赖缺失应报告失败/未跑，不把 skip 当通过。

本 README 不使用固定“全部问题已修复”或过期通过计数。以本次实际测试报告为准；代码回归、隔离全量验收、现库迁移/现场验收、支付接入和发布是不同阶段。本次不发布 Pages / KBase。

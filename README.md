# 知核（Zhihe）

知核是面向生物科研工作流的工具包发布与学习平台。作者可以组织教程、配图、原始教学文档和配套资源；学习者可以浏览、预览和学习内容；管理员可进行平台管理。

项目采用 **React / TypeScript / Vite** 单页前端和 **Flask / SQLAlchemy** API。开发时由 Vite 提供前端并代理 `/api`；生产时由 Flask/Gunicorn 提供 API 和已构建的前端文件。生产数据库为 MySQL 8；测试使用隔离数据库，部分本地场景也可显式选择 SQLite。

## 目录结构

```text
.
├── .github/workflows/ci.yml       # GitHub Actions 隔离验收
├── README.md
├── start.sh                       # 明确的前台 dev/prod 启动入口
├── backend/
│   ├── .env.example               # 配置变量示例，不含可用密钥
│   ├── app.py                     # Flask 应用、API 路由和应用初始化辅助函数
│   ├── config.py                  # 配置读取与生产安全约束
│   ├── extensions.py              # SQLAlchemy/JWT 扩展
│   ├── models.py                  # 用户、工具包、订单、文件和批处理等模型
│   ├── access.py                  # 内容可见性、学习及下载权限
│   ├── security.py                # 身份、令牌吊销和速率限制
│   ├── money_service.py           # 钱包与订单模拟账务
│   ├── file_service.py            # 上传登记、引用、校验与安全回收
│   ├── document_converter.py      # 教学文档转换和预览
│   ├── batch_kit.py               # ZIP 清单格式及校验
│   ├── batch_worker.py            # 异步批量任务处理
│   ├── batch_publish_cli.py       # 本地批量包 init/validate/pack 工具
│   ├── migrate.py                 # 显式数据库与文件迁移工具
│   ├── rebuild_lesson_previews.py # 教程预览重建维护工具
│   ├── static_spa.py              # 生产前端静态文件路由
│   ├── wsgi.py                    # Gunicorn 生产应用工厂
│   ├── requirements*.txt          # Python 运行/开发依赖
│   └── tests/                     # 后端 API、隔离、安全与迁移回归测试
└── frontend/
    ├── package.json               # 前端命令与依赖
    ├── package-lock.json
    ├── vite.config.ts             # Vite 配置和开发 API 代理
    ├── src/
    │   ├── App.tsx                # 前端路由
    │   ├── pages/                 # 页面
    │   ├── components/            # 可复用 UI 与文档阅读器
    │   └── test/                  # Vitest/Testing Library 组件测试
    ├── e2e/                       # Playwright 浏览器验收
    └── playwright.config.ts
```

目录中的缓存、虚拟环境、`node_modules`、`frontend/dist`、测试报告和业务上传文件属于本地生成物、依赖或运行数据，不是源码的一部分；请先确认用途再清理。不要将 `backend/.env`、数据库备份或上传内容提交到版本库。

## 功能与业务边界

- 浏览、搜索和筛选已发布工具包；工具包由有序教程步骤、配图、原始教学文档和配套资源组成。
- 作者可发布、保存草稿、管理教程与资源；管理者可执行平台管理操作。用户可收藏工具包、评分、记录学习进度并查看订单和钱包页面。
- 免费且可见的教程可匿名学习；**配套资源下载仍需登录**。付费内容按订单权益控制，试看只开放标记为可预览的教程，不因此开放付费附件。
- 普通下架不会收回已购买用户的学习和下载权益。存在订单或购买记录的工具包不会直接删除，以保留业务凭证。
- 平台尚未接入第三方支付。`WALLET_MODE=simulation` 仅用于开发/测试，不代表真实充值、提现、支付或可兑付资金；生产必须使用 `WALLET_MODE=disabled`。生产禁用模拟资金接口和新的付费订单/待付款支付流程。作者分成比例为 20%，但模拟账本不代表真实资金结算。
- 退款功能关闭，退款接口拒绝执行；历史退款记录和金额作为历史数据保留，不自动补账或改写权益。
- 演示 seed 会创建可预测的演示账号，只可在自有隔离的开发数据库中显式使用，**不可用于生产**。

## 前端阅读格式

教程文档阅读组件目前支持以下类型：

- Markdown（`.md`）
- Word（`.docx`）
- PDF（`.pdf`）
- HTML（`.html` / `.htm`）
- RTF（`.rtf`）
- PowerPoint（`.pptx`）

文档转换和阅读分别位于 `backend/document_converter.py` 与 `frontend/src/components/DocumentRenderer.tsx`。支持预览不代表原始文件公开：原件和配套资源仍受登录、作者身份或购买权益检查保护。SVG、HTML 等内容也受上传验证和安全渲染约束。

## 环境要求与安装

建议环境：

- Python 3.12
- Node.js 22.x（22.12 或更新的 22.x 版本）
- MySQL 8.0（生产环境；MySQL 集成验收需要可执行的 `mysqld`）
- Playwright Chromium 及其系统依赖（完整浏览器验收需要）

安装后端开发依赖和前端锁定依赖：

```bash
python3.12 -m venv backend/.venv
backend/.venv/bin/python -m pip install -r backend/requirements-dev.txt
(cd frontend && npm ci)
```

`npm ci` 按 `frontend/package-lock.json` 安装。前端命令需在 `frontend/` 目录执行。项目没有 Dockerfile 或 Compose 配置；以下启动说明基于仓库提供的 `start.sh`、Flask/Gunicorn 工厂和 CI 脚本。

## 配置

配置解析与生产安全限制见 `backend/config.py`；变量名称可参考 `backend/.env.example`。示例中的占位值不是可用于生产的密钥或密码。默认不加载 dotenv；不要提交本地 `.env`。

| 变量 | 用途与要求 |
| --- | --- |
| `APP_ENV` | `development`、`test` 或 `production`；生产 WSGI 工厂要求显式设置为 `production`。 |
| `LOAD_DOTENV` | 默认关闭。只有正常应用配置显式设为真时，才从 `backend/.env` 加载；测试注入配置和迁移 CLI 不读取该文件。 |
| `SQLALCHEMY_DATABASE_URI` / `DATABASE_URL` | 显式数据库连接 URI。生产必须提供此项，或设置完整的 MySQL 连接变量。密码如含特殊字符，应先按 URI 规则编码。 |
| `MYSQL_HOST`、`MYSQL_PORT`、`MYSQL_DATABASE`、`MYSQL_USER`、`MYSQL_PASSWORD` | MySQL 连接变量；端口默认 `3306`。生产须提供非空主机、库名、用户和密码。不要依赖开发默认数据库配置。 |
| `JWT_SECRET_KEY` | 非测试环境必填；使用至少 32 字符的高熵随机值，禁止已知占位值。JWT 仅经 `Authorization: Bearer …` 请求头传递。 |
| `JWT_EXPIRE_DAYS` | JWT 有效期天数，默认 `1`。 |
| `RATE_LIMIT_SECRET` | 速率限制键的摘要密钥；未设置时回退使用 JWT 密钥，建议使用单独的强随机值。 |
| `UPLOAD_FOLDER` | 私有、持久化的文件存储根目录；默认是 `backend/uploads`。生产应配置在 Web 静态目录之外。 |
| `FRONTEND_ORIGIN` | 前端来源，开发默认 `http://localhost:4000`；生产设置为实际站点来源。 |
| `WALLET_MODE` | `disabled` 或 `simulation`；默认 `disabled`。生产禁止 `simulation`。 |
| `DEBUG` / `FLASK_DEBUG`、`DEMO_SEED` | 调试和演示数据开关；默认关闭，生产禁止开启。 |
| `TRUSTED_PROXY_CIDRS` | 可选的可信代理 CIDR 列表。不要信任任意来源传入的转发头。 |
| `RATE_LIMIT_CAPACITY` | 速率限制桶容量，默认 `10000`。 |
| `FILE_USER_QUOTA` | 单用户登记文件配额，默认 1 GiB。 |
| `FILE_MAX_SIZE` | 单个登记资产的上限，默认 40 MiB；整次请求另有 100 MiB 上限，原始教程文档的规则不能简单等同于此单文件值。 |
| `IMAGE_MAX_PIXELS`、`IMAGE_MAX_DIMENSION` | 图片像素及单边尺寸限制，默认分别为 4000 万像素和 16000。 |
| `FILE_OPERATION_GRACE_SECONDS` | 文件操作恢复宽限期，默认 3600 秒。 |
| `GUNICORN_BIND`、`GUNICORN_WORKERS`、`GUNICORN_TIMEOUT` | 生产监听地址、worker 数和请求超时；默认分别为 `127.0.0.1:6000`、`2`、`120` 秒。优雅关闭超时为 30 秒。 |
| `BATCH_WORKERS`、`BATCH_CLAIM_TIMEOUT_SECONDS` | 每个 WSGI 进程启动的批处理 worker 数（默认 1）及过期任务认领超时（默认 600 秒）。 |

非测试环境必须设置有效的强 `JWT_SECRET_KEY`。生产配置还必须显式选择数据库，并关闭调试、演示 seed 和模拟钱包。不要把密钥、数据库连接密码或真实生产配置复制进 README、终端记录或 Git。

## 本地开发

`./start.sh dev` 只以前台方式启动 Flask（`127.0.0.1:6000`）和 Vite（`127.0.0.1:4000`），Vite 将 `/api` 请求代理到后端。可通过 `ZHIHE_DEV_API_URL` 覆盖代理目标。脚本不启动或管理 MySQL、不建表、不迁移、不 seed、不初始化上传目录，也不会后台 nohup 或停止其他进程。按 `Ctrl-C` 只停止本次脚本启动的子进程。

下面是使用**独立临时 SQLite 数据库和上传目录**的完整示例；不会使用现有业务数据库或素材：

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

# 先做只读评估，再检查报告并明确初始化这个新空库。
"$PYTHON_BIN" backend/migrate.py --dry-run > "$DEV_ROOT/review.json"
REVISION="$("$PYTHON_BIN" -c 'import json,sys; print(json.load(open(sys.argv[1]))["revision"])' "$DEV_ROOT/review.json")"
"$PYTHON_BIN" backend/migrate.py --apply \
  --expected-database "$DEV_ROOT/development.sqlite" --expected-revision "$REVISION" \
  --initialize-empty --maintenance-confirmed

# 上传目录初始化也是显式操作，不由应用工厂或启动脚本完成。
PYTHONPATH=backend "$PYTHON_BIN" -c 'from app import create_app, init_storage; app=create_app(); ctx=app.app_context(); ctx.push(); init_storage(); ctx.pop()'
./start.sh dev
```

保留这次生成的 `DEV_ROOT` 路径可复用本地数据；不要为已有业务库照搬 `--initialize-empty`。可选 seed 仅用于自有隔离开发库：

```bash
DEMO_SEED=1 PYTHONPATH=backend "$PYTHON_BIN" -c 'from app import create_app, seed_data; app=create_app(); ctx=app.app_context(); ctx.push(); seed_data(); ctx.pop()'
```

如需 MySQL 开发环境，由操作者预先准备独立数据库和专用账号，并在迁移前检查 dry-run 报告。启动脚本不会创建数据库、管理 MySQL 服务或初始化已有数据。

## 生产启动

生产服务通过 Gunicorn 运行 `backend/wsgi.py` 中的 `wsgi:create_app()`；Flask 通过 `backend/static_spa.py` 提供构建后的 SPA。部署前先准备持久数据库、私有上传目录、可恢复备份和强随机密钥；数据库迁移必须按下一节单独审查。构建前端：

```bash
(cd frontend && npm ci && npm run build)
```

构建后必须存在 `frontend/dist/index.html`。生产进程的环境变量应至少显式设置：

```dotenv
APP_ENV=production
LOAD_DOTENV=0
DEBUG=0
DEMO_SEED=0
WALLET_MODE=disabled
JWT_SECRET_KEY=<至少32字符的高熵随机密钥>
RATE_LIMIT_SECRET=<另一个高熵随机密钥>
SQLALCHEMY_DATABASE_URI=<由运维安全注入的MySQL连接URI>
UPLOAD_FOLDER=/srv/zhihe/private-uploads
FRONTEND_ORIGIN=https://<实际站点域名>
```

以上为配置形状示意；部署时须替换占位符，并使用受控方式注入机密。随后运行：

```bash
./start.sh prod
```

默认监听 `127.0.0.1:6000`、启动 2 个 Gunicorn worker，使用 120 秒请求超时和 30 秒优雅关闭超时。通过 `GUNICORN_BIND`、`GUNICORN_WORKERS`、`GUNICORN_TIMEOUT` 可显式调整。应由进程管理器和 TLS 终止反向代理管理服务；通常让站点和 `/api` 同源。生产不使用 Vite dev server 或 `vite preview`。

WSGI 工厂要求 `APP_ENV=production`，并在前端 build 不存在或生产配置不安全时拒绝启动。应用工厂不会建表、迁移、seed、初始化上传存储或自动恢复文件操作。未知 `/api` 路径返回 API JSON 404，不回退到 SPA 首页；静态服务也不会公开源码目录、隐藏路径、符号链接或路径穿越结果。

每个 Gunicorn 进程默认启动一个批处理 worker。多主机部署除共享数据库外，还必须让 worker 可读取上传目录中的待处理 ZIP 文件，或另行设计可靠的任务文件分发机制。反向代理不得为 `UPLOAD_FOLDER` 建立绕过 API 权限检查的静态 alias。

## 数据库迁移与维护

`create_app()` 不会自动连库建表；`init_database()` 的 `create_all()` 仅用于明确的空库初始化，不是既有数据库升级方式。支持的数据库和素材迁移入口是 `backend/migrate.py`。

### 迁移前

1. 核实数据库身份、上传根目录和代码版本；停止应用写入。
2. 由有权限的运维人员创建并验证可恢复的数据库及完整上传文件备份。备份应位于私有目录，不应放进 Web 根或版本库。
3. 显式提供 `SQLALCHEMY_DATABASE_URI`（或 `DATABASE_URL`）及 `UPLOAD_FOLDER`。迁移 CLI 不读 `.env`、不默认连接业务库，也不会把连接 URI/密码写进报告。
4. 默认 dry-run 只读，先保存并检查 review 报告：

```bash
export SQLALCHEMY_DATABASE_URI='<受控注入的数据库URI>'
export UPLOAD_FOLDER='<明确的上传根目录>'
backend/.venv/bin/python backend/migrate.py --dry-run > /path/to/private/review.json
```

应用前根据报告中的**准确数据库身份和最新 revision**，再次确认停写、备份及迁移计划：

```bash
backend/.venv/bin/python backend/migrate.py --apply \
  --expected-database '<报告中的MySQL库名或SQLite绝对路径>' \
  --expected-revision '<刚核对的revision>' \
  --backup-confirmed --maintenance-confirmed
```

只有全新、独立的空库才使用 `--initialize-empty`；这不是跳过既有数据库备份或迁移审查的选项。数据库身份不符、review 过期、确认不足或 schema 不兼容时，迁移会拒绝继续。

### 迁移与恢复边界

- 迁移会逐项检查 schema、文件资产和引用关系；金融余额、订单、购买权益、历史流水及异常只做核验和报告，不自动补账、退款、收回权益或猜测数据归属。
- 文件用途依据工具包封面、教程图片和资源引用关系判断，不根据扩展名推断公开权限。未知所有者、歧义、冲突、缺失文件、越界路径和符号链接都会保守阻断或报告，不会广泛清理孤儿文件。
- 迁移有步骤日志，文件操作也有持久记录；中断后先重新 dry-run 并核对已完成步骤及保留文件，再按新 revision 继续处理。
- **MySQL DDL 可能隐式提交，整个迁移不是一个可整体回滚的事务。**失败后应保持停写，核查部分 schema、文件操作与数据快照；需要回退时恢复与现场匹配的备份，不能假设一次 SQL rollback 可撤销全部操作。
- token 版本升级后用户需重新登录。SQLite 迁移支持只针对具名、已存在的绝对路径数据库文件；MySQL 集成测试使用独立临时数据库。

`backend/rebuild_lesson_previews.py` 是另一个独立维护工具，默认 dry-run。实际应用时按工具要求指定教程 ID，并提供项目及上传根目录之外、已存在且权限为 `0700` 的私有备份目录。不要把预览重建或 `init_storage()`、`recover_file_operations()` 当作普通应用启动副作用。

## 文件存储与安全

上传数据不是前端静态资源。`UPLOAD_FOLDER` 使用按用途隔离的目录（如 `image`、`resource`、`asset`，以及教程原件和暂存区）；请为其配置私有、持久化存储，禁止通过 Web 服务器静态映射直接暴露。

- 匿名读取封面仅适用于登记为公开图片且确有可见封面引用的文件；教程图片、教程原件和资源下载仍需按学习权益、购买权限、作者身份或管理权限校验。
- 上传会校验名称、格式、大小及文件内容；图像有内容/尺寸限制，SVG 有额外约束，危险的可执行资源扩展名会被拒绝。用户上传的脚本只作为附件，不会在服务器执行。
- 请求体总大小默认上限为 100 MiB；登记资产默认单文件上限为 40 MiB；用户文件配额默认 1 GiB。各类原始教程文件和 ZIP 还受各自结构及读取预算限制，以上数值不能互相替代。
- 密码变更会提升用户 token 版本并使旧会话失效；速率限制状态保存在数据库。受保护的响应使用禁止缓存等安全头，文件响应还会设置内容类型保护。
- JWT 只能通过 `Authorization: Bearer …` 请求头发送，不能拼入图片或下载 URL。不要把上传目录、生产数据或备份放入 `frontend/public`、`frontend/dist` 或其他公开目录。

## 批量发布

页面入口为“我的 → 我的工具包 → 批量上传”，路由为 `/publish/batch`。API 可获取模板及转换辅助信息：

```text
GET  /api/batch/template
GET  /api/batch/skill
GET  /api/batch/prompt
POST /api/batch/upload
GET  /api/batch/jobs
GET  /api/batch/jobs/{job_id}
```

批量包为 ZIP，清单文件是 UTF-8 的 `toolkits.json`，教程正文、封面和资源由清单引用。先用 `backend/batch_publish_cli.py` 的 `init`、`validate`、`pack` 命令在本地生成和检查包。转换/打包只处理清单引用的安全文件，不应包含秘密文件、符号链接或输出 ZIP 本身。

上传接口以 HTTP `202` 返回任务 ID，后台 worker 异步执行；客户端通过 `/api/batch/jobs/{job_id}` 轮询状态和逐项结果。任务和认领状态由数据库持久化并原子更新；ZIP 暂存文件位于 `UPLOAD_FOLDER` 下，需确保 worker 能持续访问。结果区分 `created`、`failed`、`skipped` 等状态。

当前包和清单还受多层限制，包括最多 50 个工具包、每个工具包最多 50 个教程、每个工具包最多 20 个资源、请求/压缩包上限 100 MiB、展开总量上限 300 MiB、ZIP 成员上限 2000、单个成员上限 40 MiB，以及清单和文本长度限制。服务端还会校验安全路径、重复路径、图像内容、资源引用和文件预算；具体约束以 `backend/batch_kit.py` 与模板内说明为准。

## 主要 API 路由

API 前缀为 `/api`。仓库当前没有 OpenAPI/Swagger 规格；以下是主要路由族的导航摘要，字段和权限以 `backend/app.py` 与 `backend/access.py` 实现为准。认证令牌使用 `Authorization: Bearer <token>` 请求头。

| 区域 | 路由摘要 |
| --- | --- |
| 状态与发现 | `GET /health`、`GET /stats`、`GET /toolkits`、`GET /toolkits/{id}` |
| 认证与账户 | `POST /auth/register`、`POST /auth/login`、`POST /auth/logout`、`GET /auth/me`、`PUT /auth/password` |
| 工具包发布 | `POST /toolkits`、`PUT /toolkits/{id}`、`PUT /toolkits/{id}/status`、`DELETE /toolkits/{id}` |
| 教程与文件 | `POST /lessons/{id}/complete`、`GET /uploads/{stored}`、`GET /lesson-asset/{stored}`、`GET /lesson-raw/{lesson_id}`、`GET /lesson-preview/{lesson_id}` |
| 资源下载 | `GET /toolkits/{id}/download`、`GET /toolkits/{id}/resources/{ref_id}/download` |
| 收藏与评分 | `POST /toolkits/{id}/favorite`、`PUT /toolkits/{id}/rating`、`GET /me/favorites`、`GET /me/lesson-progress` |
| 个人工具包 | `GET /me/toolkits` |
| 订单与钱包 | `POST /orders`、`POST /orders/{id}/pay`、`POST /orders/{id}/cancel`、`GET /me/orders`、`GET /wallet`、`POST /wallet/recharge`、`POST /wallet/withdraw` |
| 管理 | `GET /admin/stats`、`GET /admin/toolkits`、`GET /admin/orders`、`POST /admin/toolkits/{id}/featured`、`POST /admin/orders/{id}/refund`（退款关闭） |

工具包列表支持关键词、分类、价格、排序与分页等参数。不要将前端当前进行的 `featured` 本地过滤误认为服务端支持同名筛选参数。免费内容的读取和资源下载权限不同，调用文件接口时始终遵循后端授权结果。

## 测试、构建与 CI

常用命令（从仓库根目录执行）：

```bash
# 后端测试，包括隔离数据库与迁移回归
backend/.venv/bin/python -m pytest backend/tests

# 后端最小隔离动态夹具 smoke 检查；不是完整验收
backend/.venv/bin/python backend/smoke_test.py

# 前端 TypeScript 检查、组件测试和构建
(cd frontend && npm run typecheck)
(cd frontend && npm test)
(cd frontend && npm run build)

# Playwright 浏览器测试；使用验收 harness 的隔离环境，不要指向业务后端
(cd frontend && npm run test:e2e)

# 完整隔离验收，含临时 MySQL、后端、审计、前端及浏览器步骤
backend/.venv/bin/python backend/acceptance_test.py

# 仅后端子集，不等同于完整验收
backend/.venv/bin/python backend/acceptance_test.py --backend-only

# 启动脚本语法
bash -n start.sh
```

前端脚本定义于 `frontend/package.json`：`dev`、`typecheck`、`test`、`test:watch`、`test:e2e`、`build`、`preview`。`npm run build` 包括 TypeScript build 检查和 Vite 构建；`npm run preview` 是本地预览工具，不是生产服务入口。

完整验收的 MySQL 测试需要 `mysqld`，浏览器测试需要 Playwright Chromium 及系统依赖。准备浏览器：

```bash
(cd frontend && npx playwright install --with-deps chromium)
```

`backend/acceptance_test.py` 会创建隔离临时数据库、临时上传目录和独立测试服务，运行后端回归、smoke、依赖审计、前端组件/build 和 Playwright 验收；不会复用业务数据库、现有 uploads 或外部 E2E URL。MySQL 集成测试需要 `mysqld` 可用；缺少依赖、测试跳过、失败或浏览器 flaky 重试都不能算作验收通过。产物保存在新的 `.test-artifacts/acceptance-*/` 目录中，不纳入 Git。

CI 配置位于 `.github/workflows/ci.yml`：Ubuntu 24.04、Python 3.12、Node 22，安装 MySQL server 核心二进制而不启动系统数据库服务，然后运行完整隔离验收。修改 CI 文件本身不会触发远端运行；以真实 CI 结果和本地实际测试输出为准，不在 README 中维护过期的通过计数。

## 开发约定

- 数据库 schema、上传存储和历史业务数据都通过有明确范围的维护命令管理，不把迁移、seed 或文件恢复隐藏在 Web 应用启动路径中。
- 新增或调整文件访问逻辑时，同时验证匿名、作者、管理员和已购买用户的权限边界，并确保不会把私有上传根公开。
- 为 API、业务流程、文件生命周期和并发行为补充对应测试；前端交互使用组件测试，跨页面流程使用隔离 Playwright 验收。
- 变更后至少运行与改动相关的测试；发布或迁移须分别完成对应的现场审查、备份和授权流程。测试通过不等于已经迁移生产数据库、部署或接入真实支付。

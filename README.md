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

## 业务约定

- 余额充值为演示支付流程，真实上线需接入第三方支付回调。
- 购买付费工具包时，作者获得订单实付金额的 20% 分成。
- 免费工具包登录后可直接学习；付费工具包购买后解锁资源与完整教程。
- 超级管理员可查看平台概览、审核/下架工具包；普通用户可发布和管理自己的工具包。


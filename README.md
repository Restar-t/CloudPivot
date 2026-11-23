# CloudPivot 云枢网盘

大数据存储技术课程项目（仓库代号 netdisk）。目标：以"优"级标准实现一个具备**可靠大文件传输、对象存储去重、
版本管理、细粒度分享与 RBAC** 的网盘系统。

- 设计依据：仓库外《PLAN.md》《CODING_PLAN.md》
- 当前里程碑：**M0 骨架与横切基础**（完成）

## 技术栈

| 层 | 选型 |
|---|---|
| 后端 | FastAPI + SQLAlchemy 2.0(async) + Alembic，Python 3.11 |
| 数据库 | MySQL 8（utf8mb4） |
| 缓存 | Redis 7 |
| 对象存储 | MinIO（formal / candidate 双桶） |
| 前端 | React 18 + TypeScript + Vite + Ant Design 5 |
| 部署 | Docker Compose 全组件编排 |

## 一键启动（推荐）

前置：Docker 与 Docker Compose v2。

```bash
cp .env.example .env    # 按需修改密码（本地体验默认值即可）
docker compose up -d --build
```

启动完成后：

| 服务 | 地址 |
|---|---|
| 前端 | http://127.0.0.1:8080 |
| 后端 API | http://127.0.0.1:8000/api（首页可视化展示 /health） |
| API 文档 | http://127.0.0.1:8000/docs |
| MinIO 控制台 | http://127.0.0.1:9001 |

冒烟验证（应全绿）：

```bash
python scripts/smoke.py
```

## 本地开发（不依赖 Docker）

后端（Python 3.11+，外部依赖指向本地服务，缺失时应用仍可启动、/health 显示 degraded）：

```bash
cd backend
python -m venv .venv
.venv/Scripts/pip install -r requirements-dev.txt   # Windows
# .venv/bin/pip install -r requirements-dev.txt     # Linux/macOS
.venv/Scripts/python -m pytest tests                # 单测
.venv/Scripts/uvicorn app.main:app --reload --port 8000
```

前端（Node 18+）：

```bash
cd frontend
npm install
npm run dev    # http://localhost:5173，/api 自动代理到 127.0.0.1:8000
npm run build  # 产物 dist/，由 nginx 容器托管
```

## 目录结构

```
netdisk/
├── docker-compose.yml      # 全组件编排（M0 起纳入版本控制）
├── .env.example            # 配置模板（不含真实密码）
├── Makefile / scripts/     # 常用命令 + 冒烟脚本
├── backend/                # FastAPI：app 工厂/core/db/api/storage + alembic + tests
├── frontend/               # Vite+React+AntD：api/pages/layouts + nginx.conf
└── docs/                   # design（ER/状态机/权限矩阵）…随里程碑扩充
```

## 里程碑进度

- [x] M0 骨架与横切基础（compose 一键起 / 冒烟绿 / 前后端联通）
- [x] M1 认证与账号（注册/登录/刷新轮换+重用检测/退出/改密/爆破防护；32 个单测）
- [ ] M2 空间与目录树（RBAC 地基）
- [ ] M3 单文件闭环
- [ ] M4 回收站/移动/搜索/ZIP
- [ ] M5 分享与预览
- [ ] M6 分片上传/断点续传/秒传
- [ ] M7 版本与一致性治理
- [ ] M8 异步任务/限速/团队空间
- [ ] M9 加固、性能与交付

## 已知边界（当前）

- **compose 运行验证延后**：本机未安装 Docker，`docker compose up` 的官方验证推迟到
  M3 / M6 / M9 三个检查点执行（中级闭环、可靠传输、交付定稿各一次）；期间每个里程碑
  仍同步维护 compose 与 Dockerfile（本地以单测 + 本地服务验证代替）。M0 的本地等价验证
  已完成：后端启动 / 冒烟脚本红绿逻辑 / 前后端联通（浏览器实测）。
- Windows 本机若开启系统代理/TUN，MinIO 客户端对回环地址的探测可能被代理拦截；后端已内置
  显式连接/读超时与调用级兜底超时（`MINIO_CALL_TIMEOUT`），不会挂死启动。容器内无此问题。

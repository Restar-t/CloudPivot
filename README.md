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
- [x] compose 全栈验证（五容器 healthy、/health 三依赖 up、重启持久化、浏览器实测）
- [x] M1 认证与账号（注册/登录/刷新轮换+重用检测/退出/改密/爆破防护）
- [x] M2 空间与目录树（个人空间/目录 CRUD/分页排序/面包屑/重命名 generation/越权 404 隔离）
- [x] M3 单文件闭环（三层数据模型：user_file→file_version→storage_object；流式上传
      SHA-256 核验/物理去重/完整 Range 下载/删除进回收站/配额事务校验；84 测试 +
      容器栈冒烟全绿 + 浏览器 e2e）← **"中"级 8 项功能全部就位**
- [x] M4 回收站/移动/复制/批量/搜索/ZIP（还原含子树、彻底删除释放引用与配额、
      移动循环检测、复制自动命名+ref_count++、ZIP 流式打包逐文件鉴权、搜索通配符转义；
      100 测试 + 浏览器 e2e）← **"良"级 8 项中 5 项落地**
- [x] M5 分享与在线预览（随机 token+bcrypt 提取码+防爆破、有效期/次数限制按会话计数、
      撤销 epoch 语义 T04、名额原子扣减+幂等 T06、五点实时校验、传输中周期复核、
      魔数识别预览（图片/PDF/文本 inline，SVG/HTML 强制下载）、公开页免登录；
      112 测试 + 浏览器 e2e）← **"良"级 8 项全部闭合**
- [ ] M2 空间与目录树（RBAC 地基）
- [ ] M3 单文件闭环
- [ ] M4 回收站/移动/搜索/ZIP
- [ ] M5 分享与预览
- [ ] M6 分片上传/断点续传/秒传
- [ ] M7 版本与一致性治理
- [ ] M8 异步任务/限速/团队空间
- [ ] M9 加固、性能与交付

## 已知边界（当前）

- **MinIO 渠道变更**：MinIO 社区版已停摆（2025 源码化 → 2026-04 仓库归档 → 2026-09 Docker Hub
  `minio/minio` 镜像删除、dl.min.io 二进制全部 410）。本项目 compose 改用
  `bitnamilegacy/minio:2025.7.23-debian-12-r5`（Bitnami 冻结镜像，内含真 MinIO 服务端，
  版本固定保证可复现）；应用侧使用 minio-py SDK 走标准 S3 协议，不受影响。
- 构建镜像内 pip/npm 已切换清华源/npmmirror（国内直连稳定），拉取基础镜像需代理或加速器（仅首次）。
- Docker Desktop 代理为手动模式指向 `127.0.0.1:7890`（改机时需按实际代理调整或删除
  `%APPDATA%\Docker\settings-store.json` 中的 OverrideProxy* 键）。

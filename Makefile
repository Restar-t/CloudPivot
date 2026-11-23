.PHONY: up down logs smoke backend-test backend-dev frontend-dev

# 一键启动全组件（首次需先 cp .env.example .env）
up:
	docker compose up -d --build

down:
	docker compose down

logs:
	docker compose logs -f backend

# 冒烟脚本（M0：健康检查；随里程碑逐步扩充）
smoke:
	python scripts/smoke.py

# 本地开发（不依赖 Docker 的后端单测）
backend-test:
	cd backend && python -m pytest tests -q

backend-dev:
	cd backend && uvicorn app.main:app --reload --port 8000

frontend-dev:
	cd frontend && npm run dev

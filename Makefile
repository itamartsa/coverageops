# CoverageOps – Developer Makefile
# Usage: make <target>

.PHONY: help dev prod seed logs shell-api shell-db test clean

help:
	@echo ""
	@echo "  CoverageOps – פקודות פיתוח"
	@echo "  ─────────────────────────────────────"
	@echo "  make dev        הפעלת סביבת פיתוח"
	@echo "  make prod       הפעלת סביבת ייצור"
	@echo "  make seed       אתחול נתוני ברירת מחדל"
	@echo "  make logs       צפייה בלוגים"
	@echo "  make shell-api  כניסה ל-container backend"
	@echo "  make shell-db   כניסה ל-postgres"
	@echo "  make test       הרצת בדיקות"
	@echo "  make clean      מחיקת volumes"
	@echo ""

# ── Development (hot-reload) ──────────────────────────────────────────────────
dev:
	cp -n .env.example .env 2>/dev/null || true
	docker compose up --build db backend frontend

# ── Production (with nginx) ───────────────────────────────────────────────────
prod:
	docker compose --profile production up --build -d

# ── Seed default users ────────────────────────────────────────────────────────
seed:
	docker compose exec backend python -m app.scripts.seed

# ── Logs ──────────────────────────────────────────────────────────────────────
logs:
	docker compose logs -f --tail=100

logs-api:
	docker compose logs -f --tail=100 backend

# ── Shell access ──────────────────────────────────────────────────────────────
shell-api:
	docker compose exec backend bash

shell-db:
	docker compose exec db psql -U coverageops -d coverageops

# ── DB migrations ─────────────────────────────────────────────────────────────
migrate:
	docker compose exec backend alembic upgrade head

migration:
	docker compose exec backend alembic revision --autogenerate -m "$(MSG)"

# ── Tests ─────────────────────────────────────────────────────────────────────
test:
	docker compose exec backend pytest tests/ -v

# ── Cleanup ───────────────────────────────────────────────────────────────────
clean:
	docker compose down -v --remove-orphans
	@echo "✅ Volumes removed"

stop:
	docker compose down

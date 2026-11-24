UV ?= uv
DOCKER_COMPOSE ?= docker compose
UV_RUN = $(UV) run --active

.PHONY: up down migrate test worker beat run lint shell simulate demo \
	demo-worker demo-live demo-live-feed demo-detector demo-live-continuous \
	demo-fetch demo-chat demo-say demo-reset wait

up:
	$(DOCKER_COMPOSE) up -d db redis
	$(MAKE) wait

down:
	$(DOCKER_COMPOSE) stop

wait:
	@for service in db redis; do \
		id="$$($(DOCKER_COMPOSE) ps -q "$$service")"; \
		if [ -z "$$id" ]; then echo "$$service container was not started"; exit 1; fi; \
		for attempt in $$(seq 1 60); do \
			status="$$(docker inspect --format='{{.State.Health.Status}}' "$$id")"; \
			if [ "$$status" = healthy ]; then break; fi; \
			if [ "$$status" = unhealthy ]; then echo "$$service is unhealthy"; exit 1; fi; \
			if [ "$$attempt" -eq 60 ]; then echo "Timed out waiting for $${service}"; exit 1; fi; \
			sleep 2; \
		done; \
		echo "$$service is healthy"; \
	done

migrate:
	$(UV) sync --active
	$(MAKE) up
	set -a; . ./.env; set +a; $(UV_RUN) python manage.py migrate

test:
	$(UV) sync --active
	$(MAKE) up
	set -a; . ./.env; set +a; $(UV_RUN) pytest

worker:
	set -a; . ./.env; set +a; $(UV_RUN) celery -A easylv worker --loglevel=INFO

beat:
	set -a; . ./.env; set +a; $(UV_RUN) celery -A easylv beat --loglevel=INFO

run:
	set -a; . ./.env; set +a; $(UV_RUN) python manage.py runserver

lint:
	$(UV_RUN) ruff check .
	$(UV_RUN) ruff format .

shell:
	set -a; . ./.env; set +a; $(UV_RUN) python manage.py shell

simulate:
	set -a; . ./.env; set +a; $(UV_RUN) python manage.py simulate_comment --scenario race

demo: up
	set -a; . ./.env; set +a; ./scripts/demo.sh

demo-worker:
	set -a; . ./.env; set +a; \
	$(UV_RUN) python manage.py shell -c \
		'from django.conf import settings; assert settings.DEBUG, "Live demo requires DEBUG=True."' && \
	CELERY_WORKER_LOG_FORMAT='%(message)s' \
	CELERY_WORKER_TASK_LOG_FORMAT='%(message)s' \
	$(UV_RUN) celery -A easylv worker --loglevel=INFO

demo-live:
	@set -a; . ./.env; set +a; \
	$(UV_RUN) python manage.py shell -c \
		'from django.conf import settings; assert settings.DEBUG, "Live demo requires DEBUG=True."' && \
	printf '%s\n' \
		'Start first, Terminal 3 (worker): make demo-worker' \
		'Start second, Terminal 2 (detector): make demo-detector' \
		'Start last, Terminal 1 (player; resets demo data first): make demo-live-feed'

demo-live-feed:
	set -a; . ./.env; set +a; $(UV_RUN) python manage.py demo_live_feed $(ARGS)

demo-detector:
	set -a; . ./.env; set +a; $(UV_RUN) python manage.py demo_detector_tail $(ARGS)

demo-live-continuous:
	set -a; . ./.env; set +a; \
	$(UV_RUN) python manage.py demo_live_continuous \
		--start-code 100 --comments-per-item 30 \
		--reservation-ttl-seconds 45 --seed 42 $(ARGS)

demo-fetch:
	set -a; . ./.env; set +a; $(UV_RUN) python manage.py demo_live_fetch $(ARGS)

demo-say:
	set -a; . ./.env; set +a; $(UV_RUN) python manage.py demo_live_say $(ARGS)

demo-reset:
	set -a; . ./.env; set +a; $(UV_RUN) python manage.py demo_reset

demo-chat:
	set -a; . ./.env; set +a; $(UV_RUN) python manage.py demo_live_chat $(ARGS)

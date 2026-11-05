FROM python:3.12-slim
WORKDIR /srv
COPY apps/api/requirements.txt /tmp/requirements.txt
COPY apps/api/requirements-real.txt /tmp/requirements-real.txt
RUN pip install --no-cache-dir -r /tmp/requirements.txt -r /tmp/requirements-real.txt
COPY apps/api/app /srv/api/app
COPY apps/api/data /srv/api/data
COPY apps/worker/app /srv/worker
ENV PYTHONPATH=/srv/api:/srv/worker
CMD ["celery","-A","celery_app.celery_app","worker","--loglevel=INFO"]

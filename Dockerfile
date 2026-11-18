FROM python:3.12-slim

ENV PYTHONUNBUFFERED=1 \
    PYTHONDONTWRITEBYTECODE=1 \
    PIP_NO_CACHE_DIR=1 \
    DATA_DIR=/data
WORKDIR /app
COPY requirements.txt /tmp/requirements.txt
RUN python -m pip install --no-cache-dir -r /tmp/requirements.txt \
    && python -c "import os; os.mkdir('/data'); os.chown('/data', 568, 568)"
COPY app /app/app
COPY --chmod=755 docker-entrypoint.sh /app/docker-entrypoint.sh
COPY LICENSE THIRD_PARTY_NOTICES.md /app/
COPY licenses /app/licenses
USER 568:568
ENTRYPOINT ["/app/docker-entrypoint.sh"]
EXPOSE 8080
HEALTHCHECK --interval=30s --timeout=5s --start-period=20s --retries=3 \
    CMD python -c "import urllib.request; urllib.request.urlopen('http://127.0.0.1:8080/api/health', timeout=3).read()"
CMD ["python", "-m", "uvicorn", "app.main:app", "--host", "0.0.0.0", "--port", "8080", "--no-proxy-headers"]

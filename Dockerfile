FROM python:3.12-slim
WORKDIR /app
COPY pyproject.toml README.md ./
COPY octupus ./octupus
RUN pip install --no-cache-dir -e . && pip install --no-cache-dir 'octupus[browser]' 2>/dev/null || pip install --no-cache-dir -e .
RUN python -m playwright install --with-deps chromium || true
ENTRYPOINT ["octupus"]

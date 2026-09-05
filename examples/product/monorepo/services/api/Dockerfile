FROM registry.internal/ci/python-uv-toolkit:0.2.0
WORKDIR /app
COPY services/api/ /app/
RUN uv sync --locked --no-dev
ENV PATH="/app/.venv/bin:$PATH"
USER 10001
CMD ["python", "-m", "api"]

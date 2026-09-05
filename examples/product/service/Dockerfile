FROM registry.internal/ci/python-uv-toolkit:0.2.0
WORKDIR /app
COPY ./ /app/
RUN --mount=from=ci-dependencies,target=/ci-deps python -m pip install --no-index --find-links=/ci-deps/wheels --require-hashes -r /ci-deps/requirements.txt
USER 10001
CMD ["python", "-m", "api"]

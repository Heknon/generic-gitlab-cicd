# Customize an already prepared runtime. This is not the bootstrap factory.
# For a cold factory, use the toolkit repository's images.gitlab-ci.yml.
ARG BASE_IMAGE=artifactory.internal/ci/python:0.4.0
FROM ${BASE_IMAGE}
COPY pip.conf /etc/pip.conf
COPY uv.toml /etc/uv/uv.toml
RUN bash --version && git --version && uv --version \
    && python -c "import generic_ci; from packaging.version import Version; assert Version('0.4.0') <= Version(generic_ci.__version__) < Version('0.5.0')" \
    && python -m generic_ci schema > /dev/null

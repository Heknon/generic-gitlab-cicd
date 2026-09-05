# BASE_IMAGE must already contain Python 3.11+, Bash, Git and update-ca-certificates.
# Use an approved internally mirrored base digest. No public syntax frontend.
ARG BASE_IMAGE
FROM ${BASE_IMAGE}
USER root
COPY certificates/ /usr/local/share/ca-certificates/
RUN update-ca-certificates
ENV SSL_CERT_FILE=/etc/ssl/certs/ca-certificates.crt \
    REQUESTS_CA_BUNDLE=/etc/ssl/certs/ca-certificates.crt \
    PIP_CERT=/etc/ssl/certs/ca-certificates.crt \
    GIT_SSL_CAINFO=/etc/ssl/certs/ca-certificates.crt \
    UV_PYTHON_DOWNLOADS=never \
    UV_LINK_MODE=copy \
    PIP_DISABLE_PIP_VERSION_CHECK=1
COPY config/pip.conf /etc/pip.conf
COPY config/uv.toml /etc/uv/uv.toml
# requirements.lock must contain ALL transitive dependencies and hashes.
COPY requirements.lock /tmp/requirements.lock
COPY wheelhouse/ /tmp/wheelhouse/
RUN python -m pip install --no-index --find-links=/tmp/wheelhouse --require-hashes -r /tmp/requirements.lock \
    && python -m pip install --no-index --find-links=/tmp/wheelhouse --no-deps --require-hashes -r /tmp/wheelhouse/toolkit-requirements.txt \
    && python -m pip check \
    && uv --version \
    && bash --version
    && git --version \
    && python -m twine --version \
    && python -c "import tomlkit, packaging, build, yaml" \
    && python -m generic_ci schema > /dev/null \
    && rm -rf /tmp/wheelhouse /tmp/requirements.lock

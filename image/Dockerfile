FROM odoo:19.0

COPY image/requirements.in /requirements.in
RUN pip install --no-cache-dir --break-system-packages -r /requirements.in

USER root
RUN set -eux; \
    HELM_VERSION=v3.16.3; \
    arch="$(dpkg --print-architecture)"; \
    curl -fsSL "https://get.helm.sh/helm-${HELM_VERSION}-linux-${arch}.tar.gz" -o /tmp/helm.tgz; \
    tar -xzf /tmp/helm.tgz -C /tmp; \
    mv "/tmp/linux-${arch}/helm" /usr/local/bin/helm; \
    chmod 0755 /usr/local/bin/helm; \
    rm -rf /tmp/helm.tgz "/tmp/linux-${arch}"; \
    helm version
USER odoo

COPY image/addons/fs_minio /mnt/extra-addons/fs_minio
COPY image/addons/cloud /mnt/extra-addons/cloud
COPY infra/charts /charts
COPY image/container/entrypoint-dbbase /entrypoint-dbbase

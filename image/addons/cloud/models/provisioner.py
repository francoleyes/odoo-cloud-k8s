import base64
import json
import logging
import os
import subprocess

from kubernetes import client, config
from kubernetes.client.rest import ApiException

_logger = logging.getLogger(__name__)

CHART_DIR = os.environ.get("CLOUD_TENANT_CHART", "/charts/tenant")


def _helm_env():
    env = dict(os.environ)
    env.setdefault("HELM_CACHE_HOME", "/tmp/helm/cache")
    env.setdefault("HELM_CONFIG_HOME", "/tmp/helm/config")
    env.setdefault("HELM_DATA_HOME", "/tmp/helm/data")
    return env


def _run(cmd):
    _logger.info("cloud: running %s", " ".join(cmd))
    result = subprocess.run(cmd, capture_output=True, text=True, env=_helm_env())
    if result.returncode != 0:
        raise RuntimeError((result.stderr or result.stdout).strip())
    return result.stdout


def _set_flags(pairs, string=False):
    flag = "--set-string" if string else "--set"
    out = []
    for key, value in pairs:
        out += [flag, "%s=%s" % (key, value)]
    return out


class Provisioner(object):

    def __init__(self):
        self._loaded = False
        self._core = None
        self._custom = None

    def _ensure_config(self):
        if not self._loaded:
            try:
                config.load_incluster_config()
            except Exception:
                config.load_kube_config()
            self._loaded = True

    @property
    def core(self):
        if self._core is None:
            self._ensure_config()
            self._core = client.CoreV1Api()
        return self._core

    @property
    def custom(self):
        if self._custom is None:
            self._ensure_config()
            self._custom = client.CustomObjectsApi()
        return self._custom

    def backup_now(self, namespace, name):
        self.custom.create_namespaced_custom_object(
            group="postgresql.cnpg.io",
            version="v1",
            namespace=namespace,
            plural="backups",
            body={
                "apiVersion": "postgresql.cnpg.io/v1",
                "kind": "Backup",
                "metadata": {"name": name},
                "spec": {"cluster": {"name": "postgres"}},
            },
        )

    def backup_status(self, namespace, name):
        obj = self.custom.get_namespaced_custom_object(
            group="postgresql.cnpg.io",
            version="v1",
            namespace=namespace,
            plural="backups",
            name=name,
        )
        status = obj.get("status", {}) or {}
        return {
            "phase": status.get("phase"),
            "backup_id": status.get("backupId") or status.get("backupName"),
        }

    def _release_status(self, namespace):
        try:
            out = _run(["helm", "status", namespace, "--namespace", namespace, "-o", "json"])
        except RuntimeError:
            return None
        try:
            return json.loads(out).get("info", {}).get("status")
        except ValueError:
            return None

    def _heal_stuck_release(self, namespace):
        status = self._release_status(namespace)
        if status and ("pending" in status or status == "failed"):
            _logger.warning("cloud: cleaning stuck release %s (status=%s)", namespace, status)
            try:
                _run(["helm", "uninstall", namespace, "--namespace", namespace, "--wait"])
            except RuntimeError as exc:
                _logger.info("cloud: uninstall of stuck release failed (%s)", exc)

    def read_secret_value(self, namespace, name, key):
        secret = self.core.read_namespaced_secret(name=name, namespace=namespace)
        return base64.b64decode(secret.data[key]).decode()

    def delete_backups(self, namespace):
        import s3fs

        endpoint = os.environ.get("AWS_ENDPOINT_URL")
        key = os.environ.get("AWS_ACCESS_KEY_ID")
        secret = os.environ.get("AWS_SECRET_ACCESS_KEY")
        bucket = os.environ.get("CLOUD_BACKUP_BUCKET", "odoo-attachments")
        path = "%s/backups/%s" % (bucket, namespace)
        try:
            fs = s3fs.S3FileSystem(
                key=key, secret=secret,
                client_kwargs={"endpoint_url": endpoint},
                config_kwargs={"s3": {"addressing_style": "path"}},
            )
            if fs.exists(path):
                fs.rm(path, recursive=True)
                _logger.info("cloud: deleted backup archive at %s", path)
        except Exception as exc:  # noqa: BLE001
            _logger.info("cloud: could not delete backups at %s (%s)", path, exc)

    def provision(self, namespace, base_domain, minio_user, minio_password, subdomain,
                  clone=None, image=None, restore_from=None, restore_backup_id=None,
                  neutralize=False):
        self._heal_stuck_release(namespace)
        cmd = [
            "helm", "upgrade", "--install", namespace, CHART_DIR,
            "--namespace", namespace, "--create-namespace",
        ]
        pairs = [
            ("name", namespace),
            ("subdomain", subdomain or namespace),
            ("baseDomain", base_domain),
        ]
        if image:
            pairs.append(("image", image))
        if neutralize:
            pairs.append(("neutralize", "true"))
        if restore_from:
            pairs.append(("restore.enabled", "true"))
            pairs.append(("restore.sourceName", restore_from))
            if restore_backup_id:
                pairs.append(("restore.backupID", restore_backup_id))
        else:
            self.delete_backups(namespace)
        cmd += _set_flags(pairs)
        cmd += _set_flags([("minio.user", minio_user), ("minio.password", minio_password)], string=True)
        if clone:
            cmd += _set_flags([
                ("clone.enabled", "true"),
                ("clone.sourceNamespace", clone["source_namespace"]),
                ("clone.sourcePrefix", clone["source_prefix"]),
                ("clone.targetPrefix", clone["target_prefix"]),
            ])
            cmd += _set_flags([("clone.sourcePgPassword", clone["source_pg_password"])], string=True)
        _run(cmd)

    def deprovision(self, namespace):
        try:
            _run(["helm", "uninstall", namespace, "--namespace", namespace])
        except RuntimeError as exc:
            _logger.info("cloud: helm uninstall skipped (%s)", exc)
        try:
            self.core.delete_namespace(name=namespace)
        except ApiException as exc:
            if exc.status == 404:
                _logger.info("cloud: namespace %s already gone", namespace)
                return
            raise

    def pod_status(self, namespace):
        pods = self.core.list_namespaced_pod(namespace=namespace, label_selector="app=odoo")
        lines = []
        for pod in pods.items:
            ready = "?"
            if pod.status.container_statuses:
                ready = "/".join("1" if c.ready else "0" for c in pod.status.container_statuses)
            lines.append("%s  phase=%s  ready=%s" % (pod.metadata.name, pod.status.phase, ready))
        return "\n".join(lines) or "no odoo pods yet"

    def pod_ready(self, namespace):
        try:
            pods = self.core.list_namespaced_pod(namespace=namespace, label_selector="app=odoo")
        except ApiException:
            return False
        for pod in pods.items:
            statuses = pod.status.container_statuses or []
            if statuses and all(c.ready for c in statuses):
                return True
        return False

    def wait_pod_ready(self, namespace, timeout=240):
        import time

        for _ in range(max(1, timeout // 4)):
            if self.pod_ready(namespace):
                return True
            time.sleep(4)
        return False

    def namespace_exists(self, namespace):
        try:
            self.core.read_namespace(name=namespace)
            return True
        except ApiException as exc:
            if exc.status == 404:
                return False
            raise

    def wait_namespace_gone(self, namespace, timeout=180):
        import time

        for _ in range(max(1, timeout // 3)):
            if not self.namespace_exists(namespace):
                return
            time.sleep(3)
        raise RuntimeError("namespace %s still terminating after %ss" % (namespace, timeout))

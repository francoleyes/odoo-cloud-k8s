import logging
import os
import re

from odoo import api, fields, models
from odoo.exceptions import AccessError, UserError

from .provisioner import Provisioner

_logger = logging.getLogger(__name__)

DEFAULT_BASE_DOMAIN = "19.localhost:8090"


def _subdomain(name):
    slug = re.sub(r"[^a-z0-9.-]", "-", (name or "").strip().lower())
    return re.sub(r"-+", "-", slug).strip("-.")


def _namespace(name):
    label = re.sub(r"-+", "-", _subdomain(name).replace(".", "-")).strip("-")
    return label[:63]


class CloudInstance(models.Model):
    _name = "cloud.instance"
    _description = "Customer Odoo instance on Kubernetes"
    _inherit = ["mail.thread"]
    _order = "is_main desc, name"

    name = fields.Char(required=True, tracking=True)
    active = fields.Boolean(default=True)
    is_main = fields.Boolean(
        string="Main instance",
        default=False,
        help="The control-plane Odoo itself. It is never provisioned or deleted.",
    )
    environment = fields.Selection(
        [("production", "Production"), ("testing", "Testing")],
        default="production",
        required=True,
        tracking=True,
    )
    origin_id = fields.Many2one(
        "cloud.instance", string="Cloned from", readonly=True, ondelete="set null"
    )
    version_id = fields.Many2one(
        "cloud.odoo.version", string="Odoo version", default=lambda self: self._default_version()
    )
    test_ids = fields.One2many("cloud.instance", "origin_id", string="Test copies")
    test_count = fields.Integer(compute="_compute_test_count")
    task_ids = fields.One2many("cloud.task", "instance_id", string="Tasks")
    task_count = fields.Integer(compute="_compute_task_count")
    backup_ids = fields.One2many("cloud.backup", "instance_id", string="Backups")
    backup_count = fields.Integer(compute="_compute_backup_count")
    restore_backup_id = fields.Char(readonly=True)
    restore_source_namespace = fields.Char(readonly=True)

    subdomain = fields.Char(compute="_compute_topology", store=True)
    namespace = fields.Char(compute="_compute_topology", store=True)
    host = fields.Char(compute="_compute_topology", store=True)
    url = fields.Char(compute="_compute_url")
    state = fields.Selection(
        [
            ("draft", "Draft"),
            ("provisioning", "Provisioning"),
            ("running", "Running"),
            ("stopped", "Stopped"),
            ("error", "Error"),
        ],
        default="draft",
        required=True,
        tracking=True,
    )
    error_message = fields.Text(readonly=True)
    pod_status = fields.Text(string="Live status", readonly=True)

    _sql_constraints = [
        ("namespace_uniq", "unique(namespace)", "A cloud instance with this namespace already exists."),
    ]

    def _default_version(self):
        return self.env["cloud.odoo.version"].search([], limit=1)

    def _base_domain(self):
        return self.env["ir.config_parameter"].sudo().get_param("cloud.base_domain", DEFAULT_BASE_DOMAIN)

    def _base_parts(self):
        host, _, port = self._base_domain().partition(":")
        return host, port

    @api.depends("name", "is_main")
    def _compute_topology(self):
        host_base = self._base_parts()[0]
        for rec in self:
            if rec.is_main:
                rec.subdomain = False
                rec.namespace = "odoo"
                rec.host = host_base
            else:
                sub = _subdomain(rec.name)
                rec.subdomain = sub
                rec.namespace = _namespace(rec.name)
                rec.host = "%s.%s" % (sub, host_base) if rec.name else False

    @api.depends("host")
    def _compute_url(self):
        port = self._base_parts()[1]
        suffix = "" if port in ("", "80") else ":%s" % port
        for rec in self:
            rec.url = "http://%s%s" % (rec.host, suffix) if rec.host else False

    def _compute_test_count(self):
        for rec in self:
            rec.test_count = len(rec.test_ids)

    def _minio_credentials(self):
        user = os.environ.get("AWS_ACCESS_KEY_ID", "odoo-minio")
        password = os.environ.get("AWS_SECRET_ACCESS_KEY", "minio_secret_change_me")
        return user, password

    def _compute_task_count(self):
        for rec in self:
            rec.task_count = len(rec.task_ids)

    def _compute_backup_count(self):
        for rec in self:
            rec.backup_count = len(rec.backup_ids)

    def _enqueue(self, operation):
        self.ensure_one()
        self.env["cloud.task"]._enqueue(self, operation)

    def _is_manager(self):
        return self.env.user.has_group("cloud.group_cloud_manager")

    @api.model_create_multi
    def create(self, vals_list):
        records = super().create(vals_list)
        for rec in records:
            if not rec.is_main and rec.active:
                rec.state = "provisioning"
                rec._enqueue("provision")
        return records

    def write(self, vals):
        if "active" in vals and not vals["active"]:
            if any(rec.is_main for rec in self):
                raise UserError("The main instance cannot be archived.")
            if not self._is_manager() and any(rec.environment == "production" for rec in self):
                raise AccessError("Only a Cloud Manager can archive a production instance.")
        res = super().write(vals)
        if "active" in vals:
            for rec in self.with_context(active_test=False):
                if rec.is_main:
                    continue
                if vals["active"]:
                    rec.state = "provisioning"
                    rec._enqueue("provision")
                else:
                    rec.state = "stopped"
                    rec._enqueue("deprovision")
        return res

    def unlink(self):
        for rec in self:
            if rec.is_main:
                raise UserError("The main instance cannot be deleted from here.")
            try:
                rec._do_deprovision()
            except Exception as exc:  # noqa: BLE001
                raise UserError("Could not delete namespace %s: %s" % (rec.namespace, exc))
        return super().unlink()

    def _clone_spec(self, prov):
        self.ensure_one()
        if self.environment != "testing" or not self.origin_id:
            return None
        origin = self.origin_id
        password = prov.read_secret_value(origin.namespace, "postgres-app", "password")
        return {
            "source_namespace": origin.namespace,
            "source_pg_password": password,
            "source_prefix": "odoo-attachments/%s" % origin.namespace,
            "target_prefix": "odoo-attachments/%s" % self.namespace,
        }

    def _do_provision(self):
        self.ensure_one()
        if not self.namespace:
            raise UserError("Set a name first (it becomes the namespace and subdomain).")
        user, password = self._minio_credentials()
        prov = Provisioner()
        image = self.version_id.image or False
        neutralize = self.environment == "testing"
        if self.restore_source_namespace:
            source = self.restore_source_namespace
            prov.provision(
                self.namespace, self._base_parts()[0], user, password, self.subdomain,
                image=image, restore_from=source, restore_backup_id=self.restore_backup_id or None,
                neutralize=neutralize,
            )
            ready = prov.wait_pod_ready(self.namespace)
            self.write({
                "state": "running" if ready else "provisioning", "error_message": False,
                "restore_source_namespace": False, "restore_backup_id": False,
            })
            _logger.info("cloud: provisioned %s from backup of %s", self.namespace, source)
            return
        clone = self._clone_spec(prov)
        if clone and prov.namespace_exists(self.namespace):
            clone = None
        prov.provision(
            self.namespace, self._base_parts()[0], user, password, self.subdomain,
            clone=clone, image=image, neutralize=neutralize,
        )
        ready = prov.wait_pod_ready(self.namespace)
        self.write({"state": "running" if ready else "provisioning", "error_message": False})
        _logger.info("cloud: provisioned %s (%s)", self.name, self.namespace)

    def _do_deprovision(self):
        self.ensure_one()
        if not self.namespace:
            return
        prov = Provisioner()
        prov.deprovision(self.namespace)
        prov.delete_backups(self.namespace)
        self.backup_ids.unlink()
        _logger.info("cloud: deprovisioned %s (%s)", self.name, self.namespace)

    def _do_restore(self):
        self.ensure_one()
        prov = Provisioner()
        backup_id = self.restore_backup_id or None
        prov.deprovision(self.namespace)
        prov.wait_namespace_gone(self.namespace)
        user, password = self._minio_credentials()
        prov.provision(
            self.namespace, self._base_parts()[0], user, password, self.subdomain,
            image=self.version_id.image or False, restore_from=self.namespace,
            restore_backup_id=backup_id,
        )
        ready = prov.wait_pod_ready(self.namespace)
        self.write({
            "state": "running" if ready else "provisioning",
            "error_message": False, "restore_backup_id": False,
        })
        _logger.info("cloud: restored %s (%s)", self.name, self.namespace)

    def action_provision(self):
        for rec in self:
            if not rec.is_main:
                rec.state = "provisioning"
                rec._enqueue("provision")
        return True

    def action_restore(self):
        self.ensure_one()
        if self.is_main:
            raise UserError("The main instance can only be restored into a test copy.")
        if not self._is_manager():
            raise AccessError("Only a Cloud Manager can restore a production instance in place.")
        if self.state != "running":
            raise UserError("Only a running tenant can be restored.")
        self.write({"state": "provisioning", "restore_backup_id": False})
        self._enqueue("restore")
        return True

    def action_backup_now(self):
        self.ensure_one()
        name = "manual-%s" % fields.Datetime.now().strftime("%Y%m%d-%H%M%S")
        Provisioner().backup_now(self.namespace, name)
        self.env["cloud.backup"].create({
            "name": name,
            "instance_id": self.id,
            "backup_time": fields.Datetime.now(),
            "origin": "manual",
        })
        return {
            "type": "ir.actions.client",
            "tag": "display_notification",
            "params": {
                "type": "success",
                "message": "Backup requested (%s). CNPG is taking it now." % name,
                "sticky": False,
            },
        }

    def action_view_tasks(self):
        self.ensure_one()
        return {
            "type": "ir.actions.act_window",
            "name": "Tasks",
            "res_model": "cloud.task",
            "view_mode": "list,form",
            "domain": [("instance_id", "=", self.id)],
        }

    def action_view_backups(self):
        self.ensure_one()
        self.backup_ids._refresh()
        return {
            "type": "ir.actions.act_window",
            "name": "Backups",
            "res_model": "cloud.backup",
            "view_mode": "list,form",
            "domain": [("instance_id", "=", self.id)],
            "context": {"default_instance_id": self.id},
        }

    def action_clone_to_test(self):
        self.ensure_one()
        if self.is_main:
            raise UserError("The main instance cannot be cloned.")
        Instance = self.with_context(active_test=False)
        n = 1
        while True:
            prefix = "test" if n == 1 else "test%d" % n
            candidate = "%s.%s" % (prefix, self.name)
            if not Instance.search_count([("name", "=", candidate)]):
                break
            n += 1
        test = self.create(
            {"name": candidate, "environment": "testing", "origin_id": self.id}
        )
        return {
            "type": "ir.actions.act_window",
            "res_model": "cloud.instance",
            "res_id": test.id,
            "view_mode": "form",
            "target": "current",
        }

    def action_refresh_status(self):
        prov = Provisioner()
        for rec in self:
            try:
                rec.pod_status = prov.pod_status(rec.namespace)
                if rec.state == "provisioning" and prov.pod_ready(rec.namespace):
                    rec.state = "running"
            except Exception as exc:  # noqa: BLE001
                rec.pod_status = "error: %s" % exc
        return True

    @api.model
    def _cron_refresh_status(self):
        instances = self.search([
            ("is_main", "=", False), ("state", "in", ["running", "provisioning"]),
        ])
        if instances:
            instances.action_refresh_status()

    def action_open(self):
        self.ensure_one()
        return {"type": "ir.actions.act_url", "url": self.url, "target": "new"}

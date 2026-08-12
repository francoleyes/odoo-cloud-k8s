import logging

from odoo import api, fields, models
from odoo.exceptions import AccessError, UserError

from .provisioner import Provisioner

_logger = logging.getLogger(__name__)


class CloudBackup(models.Model):
    _name = "cloud.backup"
    _description = "Database backup of a cloud instance"
    _order = "backup_time desc, id desc"

    name = fields.Char(required=True, readonly=True)
    instance_id = fields.Many2one(
        "cloud.instance", required=True, ondelete="cascade", index=True
    )
    instance_is_main = fields.Boolean(related="instance_id.is_main")
    backup_time = fields.Datetime(required=True, readonly=True)
    backup_id = fields.Char(string="Backup ID", readonly=True)
    origin = fields.Selection(
        [("manual", "On demand"), ("scheduled", "Scheduled")],
        default="manual", readonly=True,
    )
    state = fields.Selection(
        [("requested", "Requested"), ("completed", "Completed"), ("failed", "Failed")],
        default="requested", required=True, readonly=True,
    )

    def _refresh(self):
        prov = Provisioner()
        for rec in self:
            if rec.state in ("completed", "failed") or not rec.instance_id.namespace:
                continue
            try:
                status = prov.backup_status(rec.instance_id.namespace, rec.name)
            except Exception as exc:  # noqa: BLE001
                _logger.info("cloud.backup: refresh of %s failed (%s)", rec.name, exc)
                continue
            phase = (status or {}).get("phase")
            if phase == "completed":
                rec.write({"state": "completed", "backup_id": status.get("backup_id") or rec.backup_id})
            elif phase == "failed":
                rec.state = "failed"

    def action_refresh(self):
        self._refresh()
        return True

    def action_restore(self):
        self.ensure_one()
        if self.state != "completed":
            raise UserError("This backup is not completed yet.")
        instance = self.instance_id
        if instance.is_main:
            raise UserError("The main instance can only be restored into a test copy.")
        if not instance._is_manager():
            raise AccessError("Only a Cloud Manager can restore a production instance in place.")
        if instance.state != "running":
            raise UserError("Only a running tenant can be restored.")
        instance.write({"state": "provisioning", "restore_backup_id": self.backup_id or False})
        instance._enqueue("restore")
        return {
            "type": "ir.actions.client",
            "tag": "display_notification",
            "params": {
                "type": "success",
                "message": "Restoring %s from %s…" % (instance.name, self.name),
                "sticky": False,
            },
        }

    def action_restore_to_test(self):
        self.ensure_one()
        if self.state != "completed":
            raise UserError("This backup is not completed yet.")
        source = self.instance_id
        Instance = self.env["cloud.instance"].with_context(active_test=False)
        n = 1
        while True:
            prefix = "restore" if n == 1 else "restore%d" % n
            candidate = "%s.%s" % (prefix, source.name)
            if not Instance.search_count([("name", "=", candidate)]):
                break
            n += 1
        test = self.env["cloud.instance"].create({
            "name": candidate,
            "environment": "testing",
            "origin_id": source.id,
            "restore_source_namespace": source.namespace,
            "restore_backup_id": self.backup_id or False,
        })
        return {
            "type": "ir.actions.act_window",
            "res_model": "cloud.instance",
            "res_id": test.id,
            "view_mode": "form",
            "target": "current",
        }

    @api.model
    def _cron_refresh(self):
        self.search([("state", "=", "requested")])._refresh()

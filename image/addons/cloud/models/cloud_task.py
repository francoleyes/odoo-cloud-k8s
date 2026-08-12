import logging

from odoo import api, fields, models

_logger = logging.getLogger(__name__)


class CloudTask(models.Model):
    _name = "cloud.task"
    _description = "Async operation on a cloud instance"
    _order = "id desc"

    instance_id = fields.Many2one(
        "cloud.instance", required=True, ondelete="cascade", index=True
    )
    operation = fields.Selection(
        [
            ("provision", "Provision"),
            ("deprovision", "Deprovision"),
            ("restore", "Restore"),
        ],
        required=True,
    )
    state = fields.Selection(
        [
            ("pending", "Pending"),
            ("running", "Running"),
            ("done", "Done"),
            ("error", "Error"),
        ],
        default="pending",
        required=True,
        index=True,
    )
    attempts = fields.Integer(default=0)
    max_attempts = fields.Integer(default=3)
    error_message = fields.Text(readonly=True)

    @api.model
    def _enqueue(self, instance, operation):
        task = self.create({"instance_id": instance.id, "operation": operation})
        self.env.ref("cloud.cron_run_tasks").sudo()._trigger()
        return task

    @api.model
    def _cron_run_tasks(self):
        for task in self.search([("state", "=", "pending")], order="id"):
            task._run()

    def _run(self):
        self.ensure_one()
        self.write({"state": "running"})
        self.env.cr.commit()
        inst = self.instance_id
        try:
            if self.operation == "provision":
                inst._do_provision()
            elif self.operation == "restore":
                inst._do_restore()
            else:
                inst._do_deprovision()
            self.write({"state": "done"})
        except Exception as exc:  # noqa: BLE001
            _logger.exception("cloud.task %s (%s) failed", self.id, self.operation)
            attempts = self.attempts + 1
            if attempts >= self.max_attempts:
                self.write({"state": "error", "attempts": attempts, "error_message": str(exc)})
                if self.operation in ("provision", "restore"):
                    inst.write({"state": "error", "error_message": str(exc)})
            else:
                self.write({"state": "pending", "attempts": attempts, "error_message": str(exc)})
        self.env.cr.commit()

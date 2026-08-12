from odoo import fields, models


class CloudOdooVersion(models.Model):
    _name = "cloud.odoo.version"
    _description = "Odoo version an instance can run"
    _order = "sequence, name"

    name = fields.Char(required=True)
    image = fields.Char(
        required=True,
        help="Container image + tag deployed for this version, e.g. odoo-cloud:19",
    )
    sequence = fields.Integer(default=10)
    active = fields.Boolean(default=True)

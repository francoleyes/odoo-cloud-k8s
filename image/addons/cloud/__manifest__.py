{
    "name": "Cloud",
    "version": "19.0.1.0.0",
    "summary": "Provision an isolated Odoo per customer on Kubernetes",
    "author": "Franco Leyes",
    "license": "LGPL-3",
    "depends": ["base", "mail"],
    "external_dependencies": {"python": ["kubernetes"]},
    "data": [
        "security/cloud_security.xml",
        "security/ir.model.access.csv",
        "data/cloud_data.xml",
        "views/cloud_instance_views.xml",
    ],
    "application": True,
    "installable": True,
}

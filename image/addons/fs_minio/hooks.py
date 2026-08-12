import logging

_logger = logging.getLogger(__name__)

STORAGE_CODE = "minio"


def post_init_hook(env):
    storage = env["fs.storage"].sudo().search([("code", "=", STORAGE_CODE)], limit=1)
    if not storage:
        _logger.warning("fs_minio: storage %s not found, skipping migration", STORAGE_CODE)
        return
    prefix = "%s://" % STORAGE_CODE
    attachments = env["ir.attachment"].sudo().search(
        [
            ("id", "!=", 0),
            ("store_fname", "!=", False),
            ("store_fname", "not like", prefix + "%"),
        ]
    )
    migrated = 0
    for attachment in attachments:
        data = attachment.raw
        if data is None:
            continue
        attachment.with_context(storage_location=STORAGE_CODE).write({"raw": data})
        migrated += 1
    _logger.info("fs_minio: migrated %s attachments to %s", migrated, STORAGE_CODE)

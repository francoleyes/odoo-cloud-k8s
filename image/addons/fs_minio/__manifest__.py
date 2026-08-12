{
    "name": "FS MinIO Storage",
    "summary": "Default attachment storage backend on S3-compatible object storage",
    "version": "19.0.1.0.0",
    "license": "AGPL-3",
    "author": "Franco Leyes",
    "depends": ["fs_attachment"],
    "data": ["data/fs_storage.xml"],
    "post_init_hook": "post_init_hook",
    "installable": True,
    "application": False,
}

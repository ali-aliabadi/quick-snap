from django.apps import AppConfig


class SnapConfig(AppConfig):
    default_auto_field = "django.db.models.BigAutoField"
    name = "snap"

    def ready(self):
        from django.db.backends.signals import connection_created

        def enable_wal(sender, connection, **kwargs):
            # WAL lets reads and one writer proceed concurrently — fewer locks
            # during a busy event. Only meaningful for SQLite.
            if connection.vendor == "sqlite":
                cur = connection.cursor()
                cur.execute("PRAGMA journal_mode=WAL;")
                cur.execute("PRAGMA synchronous=NORMAL;")

        connection_created.connect(enable_wal, dispatch_uid="snap_sqlite_wal")

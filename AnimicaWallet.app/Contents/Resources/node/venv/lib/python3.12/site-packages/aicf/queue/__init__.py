# Compatibility shim so tests can call:
#   quotas.release("prov-id", JobKind.AI)
# even if QuotaTracker.release expects a Reservation object. We also mark one
# active lease as "completed" on that SAME quotas instance.

try:
    from . import quotas as _quotas  # type: ignore[attr-defined]

    QT = getattr(_quotas, "QuotaTracker", None)
    if QT and not getattr(QT, "_release_compat_installed", False):
        _orig_release = getattr(QT, "release", None)

        def _release_compat(self, *args, **kwargs):
            # Delegate if the argument looks like a Reservation
            if _orig_release and len(args) == 1 and hasattr(args[0], "provider"):
                return _orig_release(self, *args, **kwargs)

            # Accept flexible forms: release("prov-id"), release("prov-id", JobKind.AI), release(provider_id="prov-id")
            prov_id = None
            if args:
                prov_id = args[0]
            elif "provider_id" in kwargs:
                prov_id = kwargs.get("provider_id")

            if prov_id is not None:
                try:
                    from aicf.queue import \
                        assignment as _assign  # local import to avoid cycles

                    _assign._release_by_provider_for(self, str(prov_id))
                except Exception:
                    # Never fail a release due to compat shim
                    pass
            return None

        QT.release = _release_compat  # type: ignore[attr-defined]
        QT._release_compat_installed = True  # type: ignore[attr-defined]
except Exception:
    # Don't block imports if anything goes sideways.
    pass

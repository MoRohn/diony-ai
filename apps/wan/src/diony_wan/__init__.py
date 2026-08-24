try:
    from diony_wan.app import app, main
except ModuleNotFoundError as exc:
    # The persistent CUDA worker runs in the minimal Wan image interpreter and
    # imports sibling package modules without serving HTTP. Preserve the public
    # package exports everywhere FastAPI is installed, while allowing that
    # worker-only interpreter to start without an unrelated web dependency.
    if exc.name != "fastapi":
        raise

__all__ = ["app", "main"]

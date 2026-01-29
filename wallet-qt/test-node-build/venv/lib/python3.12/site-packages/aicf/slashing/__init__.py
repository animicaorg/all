from .engine import Engine

# Common discovery aliases some fixtures look for:
ENGINE_CLASS = Engine
HAS_ENGINE = True


def make_engine(thresholds, penalties):
    return Engine(thresholds, penalties)


__all__ = ["Engine", "ENGINE_CLASS", "HAS_ENGINE", "make_engine"]

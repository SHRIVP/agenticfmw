def main():
    from pcagent.cli import main as m
    m()


def __getattr__(name):  # lazy, so `pcagent` stays cheap to import
    if name in ("Pack", "PackError"):
        from pcagent import packs
        return getattr(packs, name)
    raise AttributeError(name)

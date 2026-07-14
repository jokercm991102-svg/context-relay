import importlib.util
from pathlib import Path


SCRIPT = (
    Path(__file__).resolve().parents[1]
    / "skills/context-relay/scripts/run_context_relay.py"
)


def load_wrapper_module():
    spec = importlib.util.spec_from_file_location(
        "context_relay_plugin_wrapper", SCRIPT
    )
    if spec is None or spec.loader is None:
        raise AssertionError("unable to load plugin wrapper")
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module

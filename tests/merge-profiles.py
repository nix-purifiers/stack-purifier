#!/usr/bin/env python3
# SPDX-License-Identifier: MIT
"""Merge actual native profiles captured by another platform's CI runner."""

import json, sys, tempfile
from pathlib import Path

REPO = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(REPO))
from stack_purifier import lock

root = REPO / "examples/minimal"
companion = root / "stack-purifier.lock.json"
native = root / "stack.yaml.lock"
data = json.loads(companion.read_text())
assert not lock.problems(root, native, data)
for argument in sys.argv[1:]:
    incoming = json.loads(Path(argument).read_text())
    assert (
        incoming["inputs"] == data["inputs"]
        and incoming["manifests"] == data["manifests"]
    )
    with tempfile.TemporaryDirectory() as temp:
        check = Path(temp) / "native.lock"
        check.write_text(incoming["nativeLock"])
        assert not lock.problems(root, check, incoming)
    data["profiles"].update(incoming["profiles"])
data = lock.seal(data)
assert not lock.problems(root, native, data)
companion.write_text(json.dumps(data, sort_keys=True, indent=2) + "\n")

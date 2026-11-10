import json
from pathlib import Path
import sys
import yaml
sys.path.insert(0, sys.argv[4])
# The caller copies this native configuration module explicitly beside the helper.
from build import configuration
out, environment, spec_file = map(Path, sys.argv[1:4])
spec=json.loads(spec_file.read_text());profile=spec["profile"]
project=out/"project";session=out/"session";session.mkdir()
original=yaml.safe_load((project/"stack.yaml").read_text())
selected=configuration(original, environment, [str(project/p) for p in profile["projects"]])
(session/"stack.yaml").write_text(json.dumps(selected,sort_keys=True))

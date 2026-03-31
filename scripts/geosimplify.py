from shapely.geometry import shape
from shapely.ops import unary_union
import json

with open("input.geojson") as f:
    data = json.load(f)

# Simplify geometry
for feature in data["features"]:
    geom = shape(feature["geometry"])
    simplified = geom.simplify(0.00005, preserve_topology=True)
    feature["geometry"] = json.loads(json.dumps(simplified.__geo_interface__))

with open("simplified.geojson", "w") as f:
    json.dump(data, f)

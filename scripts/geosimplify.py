from shapely.geometry import shape
from shapely.ops import unary_union
import json

watershed_id = input("Enter watershed ID: ").strip()

input_file = f"{watershed_id}_microwatersheds.geojson"
output_file = f"{watershed_id}_simplified.geojson"

with open(input_file) as f:
    data = json.load(f)


for feature in data["features"]:
    geom = shape(feature["geometry"])
    simplified = geom.simplify(0.00002, preserve_topology=True)
    feature["geometry"] = json.loads(json.dumps(simplified.__geo_interface__))

with open(output_file, "w") as f:
    json.dump(data, f)

print(f"\n✅ Simplified file saved as: {output_file}")

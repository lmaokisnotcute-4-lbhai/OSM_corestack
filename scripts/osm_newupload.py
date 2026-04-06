import json
import os
import time
from datetime import datetime
import xml.etree.ElementTree as ET
import csv
from requests_oauthlib import OAuth2Session

timestamp = datetime.utcnow().isoformat()

# ==============================
# CONFIGURATION
# ==============================

client_id = os.getenv("OSM_CLIENT_ID")
with open("token.json") as f:
    token = json.load(f)

access_token = token["access_token"]

osm_api_base = "https://api.openstreetmap.org/api/0.6"

batch_size = 5   # Safe for ~500-node polygons

# ==============================
# AUTH SESSION
# ==============================

oauth = OAuth2Session(
    client_id,
    token={"access_token": access_token, "token_type": "Bearer"}
)

# ==============================
# LOAD GEOJSON
# ==============================

with open("simplified.geojson", "r") as f:
    geojson_data = json.load(f)

features = geojson_data["features"]
print("Total features:", len(features))

# ==============================
# BATCH PROCESSING
# ==============================
relation_uid_map = {}
for i in range(0, len(features), batch_size):

    batch = features[i:i + batch_size]
    print(f"\nUploading batch {i} to {i + len(batch) - 1}")

    # --------------------------
    # CREATE CHANGESET
    # --------------------------

    changeset_xml = """
    <osm>
      <changeset>
        <tag k="comment" v="Adding CoreStack metadata and microwatershed boundaries"/>
        <tag k="import" v="yes"/>
      </changeset>
    </osm>
    """

    response = oauth.put(
        f"{osm_api_base}/changeset/create",
        data=changeset_xml,
        headers={"Content-Type": "text/xml"}
    )

    if response.status_code != 200:
        print("Error creating changeset:", response.status_code)
        print(response.text)
        break

    changeset_id = response.text
    print("Changeset created:", changeset_id)

    # --------------------------
    # BUILD XML FOR THIS BATCH
    # --------------------------

    upload_parts = []
    temp_id = -1  # negative IDs must be unique per upload

    for feature in batch:

        coords = feature["geometry"]["coordinates"][0]
        uid = feature["properties"]["uid"]
        area = feature["properties"]["area_in_ha"]

        node_ids = []

        # ---- CREATE NODES ----
        for lon, lat in coords:
            upload_parts.append(
                f'<node id="{temp_id}" lat="{lat}" lon="{lon}" changeset="{changeset_id}" />'
            )
            node_ids.append(temp_id)
            temp_id -= 1

        if node_ids[0] != node_ids[-1]:
            node_ids.append(node_ids[0])

        # ---- CREATE OUTER WAY ----
        way_id = temp_id
        temp_id -= 1

        way_xml = f'<way id="{way_id}" changeset="{changeset_id}">\n'
        for nid in node_ids:
            way_xml += f'  <nd ref="{nid}"/>\n'

        way_xml += '</way>\n'
        upload_parts.append(way_xml)

        # ---- CREATE RELATION (Multipolygon) ----

        relation_id = temp_id
        relation_uid_map[relation_id] = uid
        temp_id -= 1

        wiki_url = f"https://wiki.openstreetmap.org/wiki/CoREStack/{uid}"

        relation_xml = f'''
<relation id="{relation_id}" changeset="{changeset_id}">
  <member type="way" ref="{way_id}" role="outer"/>

  <tag k="type" v="boundary"/>
  <tag k="boundary" v="watershed"/>

  <tag k="core_entity" v="microwatershed"/>
  <tag k="core_id" v="{uid}"/>
  <tag k="core_updated" v="version1"/>

  <tag k="source" v="https://core-stack.org"/>
  <tag k="created_by" v="core-stack import script"/>
  <tag k="wikipedia" v="https://wiki.openstreetmap.org/wiki/CoREStack/{uid}"/>

</relation>
'''
        upload_parts.append(relation_xml)

    # --------------------------
    # FINAL OSM CHANGE XML
    # --------------------------

    upload_xml = f"""
    <osmChange version="0.6" generator="CoreStack">
      <create>
        {''.join(upload_parts)}
      </create>
    </osmChange>
    """

    print("Batch XML size:", len(upload_xml))

    upload_response = oauth.post(
        f"{osm_api_base}/changeset/{changeset_id}/upload",
        data=upload_xml,
        headers={"Content-Type": "text/xml"}
    )

    if upload_response.status_code == 200:
        print("Batch uploaded successfully!")

        diff_xml = upload_response.text
        root = ET.fromstring(diff_xml)

        for child in root:
            if child.tag == "relation":

                old_id = int(child.attrib["old_id"])
                new_id = child.attrib["new_id"]

                uid = relation_uid_map.get(old_id, "unknown")

                print(f"MWS {uid} → OSM relation {new_id}")

                # Save to CSV
                with open("mws_osm_mapping.csv", "a", newline="") as f:
                    writer = csv.writer(f)
                    writer.writerow([uid, new_id])
    else:
        print("Upload failed:", upload_response.status_code)
        print(upload_response.text)
        break

    # --------------------------
    # CLOSE CHANGESET
    # --------------------------

    close_response = oauth.put(
        f"{osm_api_base}/changeset/{changeset_id}/close"
    )

    if close_response.status_code == 200:
        print("Changeset closed.")
    else:
        print("Error closing changeset:", close_response.status_code)

    time.sleep(5)

print("\nFinished processing.")

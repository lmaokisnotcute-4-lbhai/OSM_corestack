# CoREStack Microwatershed Pipeline

Fetch all microwatersheds within a watershed, with water balance properties (DeltaG, ET, Precipitation, RunOff, Groundwater, WellDepth), as a single GeoJSON file.

## Prerequisites

- **CoREStack API key** — generate at [dashboard.core-stack.org](https://dashboard.core-stack.org) (requires Org Admin account)
- **Google Earth Engine account** — sign up at [earthengine.google.com](https://earthengine.google.com)
- **Google Cloud Project ID** — with Earth Engine API enabled at [console.cloud.google.com](https://console.cloud.google.com)

## Setup

```bash
# 1. Clone and enter the repo
git clone https://github.com/your-username/your-repo-name.git
cd your-repo-name

# 2. Create and activate a virtual environment
python3 -m venv venv
source venv/bin/activate        # Windows: venv\Scripts\activate

# 3. Install dependencies
pip install -r requirements.txt

# 4. Authenticate with Google Earth Engine (once)
earthengine authenticate

# 5. Add your credentials
cp .env.example .env
# Open .env and fill in CORESTACK_API_KEY and GEE_PROJECT
```

## Run

```bash
python pipeline.py --watershed_id C2AGAN72
```

Optional flags:
```bash
--output my_results.geojson       # custom output filename
--gee_project my-gcp-project-id   # override GEE_PROJECT from .env
```

## Finding a Watershed ID

Watershed IDs are the `wsconc` field in CoREStack's GEE asset. Browse them in the [GEE Code Editor](https://code.earthengine.google.com):

```javascript
var ws = ee.FeatureCollection(
  "projects/corestack-datasets/assets/datasets/hydrological_boundaries/watersheds"
);
print(ws.limit(10));
Map.addLayer(ws);
```


## To push metatdata of extracted microwatersheds onto OSM

```bash
python geomsimplify.py
```
## this will create a simplified.geojson used as input in next file
```bash
python osm_auth.py
```
```bash
python osm_newupload.py
```


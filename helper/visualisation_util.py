import folium
import geopandas as gpd
import pandas as pd
from folium.plugins import MarkerCluster
from shapely.geometry import MultiPolygon, Point, Polygon


def _reproject(gdf: gpd.GeoDataFrame, crs="EPSG:4326") -> gpd.GeoDataFrame:
    """
    Reproject a GeoDataFrame to a target CRS (e.g., WGS84 for mapping).
    """
    return gdf.to_crs(crs) if gdf.crs != crs else gdf


def _extract_coords(geometry):
    """
    Extract (lat, lon) from Point geometry. Return (None, None) if invalid.
    """
    if isinstance(geometry, Point):
        return geometry.y, geometry.x
    return (None, None)


def _make_map(boundary_gdf):
    """
    Create a base Folium map centered on the boundary geometry.
    """
    return folium.Map(
        location=_get_geometry_center(boundary_gdf),
        zoom_start=11,
        tiles="OpenStreetMap",
    )


def _get_geometry_center(gdf: gpd.GeoDataFrame) -> tuple[float, float]:
    """
    Get the center point of a GeoDataFrame for map centering.
    """
    center = gdf.geometry.unary_union.centroid
    return center.y, center.x


def _add_boundary_layer(m: folium.Map, boundary_gdf: gpd.GeoDataFrame):
    """
    Add Berlin boundary layer to the map for visual context.
    """
    for _, row in boundary_gdf.iterrows():
        if isinstance(row.geometry, (Polygon, MultiPolygon)):
            folium.GeoJson(
                row.geometry.__geo_interface__,
                style_function=lambda _: {
                    "fillColor": "none",
                    "color": "gray",
                    "weight": 2,
                },
            ).add_to(m)


def _create_popup(row, label: str, fields: dict) -> str:
    """
    Create a popup HTML string for a marker.
    """
    name = row.get("name", f"Unbekannte {label}")
    html = f"<b>{label}:</b> {name}<br>"
    html += "".join(
        f"<b>{disp}:</b> {row[col]}<br>"
        for disp, col in fields.items()
        if col in row and pd.notna(row[col])
    )
    return html


def _add_markers(gdf, cluster, label, icon_color, icon_name, popup_fields):
    """
    Add individual markers (practitioners or pharmacies) to the map with clustering.
    """
    for _, row in gdf.iterrows():
        if isinstance(row.geometry, Point):
            popup = folium.Popup(_create_popup(row, label, popup_fields), max_width=300)
            icon = folium.Icon(color=icon_color, icon=icon_name, prefix="fa")
            folium.Marker(
                [row.geometry.y, row.geometry.x], popup=popup, icon=icon
            ).add_to(cluster)
        else:
            print(f"Warning: Non-Point geometry in {label} skipped.")


def plot_optimized_facility_assignments(
    practitioners_gdf: gpd.GeoDataFrame,
    pharmacies_gdf: gpd.GeoDataFrame,
    berlin_boundary_gdf: gpd.GeoDataFrame,
    open_facilities: set,
    assignments: dict,
    output_path: str = "optimized_facility_assignments_map.html",
):
    """
    Plots optimized assignments of practitioners to pharmacies using Folium.
    Includes:
        - Open pharmacies (selected in the optimization)
        - Closed pharmacies
        - Practitioners with connections to assigned pharmacies
        - Berlin boundary for context
    """
    print(f"\nCreating optimized map → {output_path}")

    # Reproject input layers to WGS84 (Folium requires lat/lon)
    practitioners = _reproject(practitioners_gdf)
    pharmacies = _reproject(pharmacies_gdf)
    boundary = _reproject(berlin_boundary_gdf)

    # Create lookup dictionaries for easier reference
    practitioners_lookup = practitioners.set_index("string_id").to_dict("index")
    pharmacies_lookup = pharmacies.set_index("string_id").to_dict("index")

    # Filter out invalid assignments (e.g., missing IDs)
    valid_assignments = {
        pr: ph
        for pr, ph in assignments.items()
        if pr in practitioners_lookup and ph in pharmacies_lookup
    }
    removed = len(assignments) - len(valid_assignments)
    if removed:
        print(f"Removed {removed} invalid assignments")

    # Create base map
    fmap = _make_map(boundary)

    # Marker clusters
    clusters = {
        "open": MarkerCluster(name="Offene Apotheken (Optimiert)").add_to(fmap),
        "closed": MarkerCluster(name="Nicht-Offene Apotheken").add_to(fmap),
        "practitioners": MarkerCluster(name="Zugewiesene Praxen").add_to(fmap),
    }

    # Polyline layer for assignments
    lines = folium.FeatureGroup(name="Zuordnungen").add_to(fmap)

    # Plot pharmacies
    for _, row in pharmacies.iterrows():
        coords = _extract_coords(row.geometry)
        if coords[0] is None:
            print(
                f"Skipping pharmacy {row.get('name', row['string_id'])} (invalid geometry)"
            )
            continue

        pid = row["string_id"]
        is_open = pid in open_facilities
        name = row.get("name", f"Apotheke {pid}")
        popup = [f"<b>Apotheke:</b> {name}<br>"]

        if is_open:
            assigned_practitioners = [
                pr for pr, ph in valid_assignments.items() if ph == pid
            ]
            popup.append(f"<b>Bedient:</b> {len(assigned_practitioners)} Praxis<br>")
            if assigned_practitioners:
                popup.append(
                    "<ul>"
                    + "".join(
                        f"<li>{practitioners_lookup[pr].get('name', pr)}</li>"
                        for pr in assigned_practitioners
                    )
                    + "</ul>"
                )
            cluster_key = "open"
            icon = folium.Icon(color="green", icon="star", prefix="fa")
        else:
            popup.append("Nicht als 'offen' in der Optimierung gewählt.<br>")
            cluster_key = "closed"
            icon = folium.Icon(color="lightgray", icon="minus-circle", prefix="fa")

        folium.Marker(
            location=coords,
            popup=folium.Popup("".join(popup), max_width=300),
            icon=icon,
        ).add_to(clusters[cluster_key])

    # Plot practitioners and their assignments
    for pr_id, ph_id in valid_assignments.items():
        pr_row = practitioners_lookup[pr_id]
        ph_row = pharmacies_lookup[ph_id]
        pr_coords = _extract_coords(pr_row["geometry"])
        ph_coords = _extract_coords(ph_row["geometry"])

        if pr_coords[0] is None or ph_coords[0] is None:
            print(f"Skipping assignment {pr_id} → {ph_id} (invalid geometry)")
            continue

        pr_name = pr_row.get("name", pr_id)
        ph_name = ph_row.get("name", ph_id)

        folium.Marker(
            location=pr_coords,
            popup=folium.Popup(
                f"<b>Praxis:</b> {pr_name}<br>Zugewiesen zu: {ph_name}",
                max_width=300,
            ),
            icon=folium.Icon(color="blue", icon="user-md", prefix="fa"),
        ).add_to(clusters["practitioners"])

        # Draw line between practitioner and assigned pharmacy
        folium.PolyLine(
            locations=[pr_coords, ph_coords],
            color="darkblue",
            weight=1.5,
            opacity=0.7,
            tooltip=f"{pr_name} → {ph_name}",
        ).add_to(lines)

    _add_boundary_layer(fmap, boundary)
    folium.LayerControl().add_to(fmap)
    fmap.save(output_path)
    print(f"Saved to {output_path}")

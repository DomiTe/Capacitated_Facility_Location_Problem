import json
import os
import time

import geopandas as gpd
import numpy as np
from pyscipopt import Model, quicksum
from shapely.geometry import Point


class NpEncoder(json.JSONEncoder):
    """
    JSON encoder to handle NumPy types for serialization.
    Inherits from json.JSONEncoder and overrides the default method.
    """

    def default(self, obj):
        if isinstance(obj, np.integer):
            return int(obj)
        elif isinstance(obj, np.floating):
            return float(obj)
        elif isinstance(obj, np.ndarray):
            return obj.tolist()
        return super(NpEncoder, self).default(obj)


def create_dummy_pharmacy_data() -> gpd.GeoDataFrame:
    """Creates a dummy GeoDataFrame of pharmacies."""
    dummy_data = {
        "name": [f"Dummy_Pharmacy_{i}" for i in range(10)],
        "geometry": [Point(13.4 + 0.01 * i, 52.5 + 0.005 * i) for i in range(10)],
    }
    return gpd.GeoDataFrame(dummy_data, crs="EPSG:32633")


def create_dummy_prac_data() -> gpd.GeoDataFrame:
    """Creates a dummy GeoDataFrame of practitioners."""
    dummy_data = {
        "name": [f"Dummy_Practitioner_{i}" for i in range(10)],
        "geometry": [Point(13.35 + 0.02 * i, 52.51 + 0.005 * i) for i in range(10)],
    }
    return gpd.GeoDataFrame(dummy_data, crs="EPSG:32633")


def solve_capacitated_flp(
    cost_matrix: dict,
    facility_capacities: dict,
    demand_quantities: dict,
    fixcost: float = 0.001,
    time_limit: int = 600,
) -> tuple[list, dict, float]:
    """
    Solves the Capacitated Facility Location Problem using PySCIPOpt.
    """
    print("\nSolving Facility Location Problem with PySCIPOpt...")

    model = Model("flp")
    model.setParam("limits/time", time_limit)

    # Sets
    demand_points_ids = list(cost_matrix.keys())
    facilities_ids = list({f_id for d in cost_matrix.values() for f_id in d})

    if not demand_points_ids or not facilities_ids:
        print("No demand points or facilities found. Exiting.")
        return [], {}, 0.0

    for d_id in demand_points_ids:
        if d_id not in demand_quantities:
            raise ValueError(f"Missing demand quantity for '{d_id}'")
    for f_id in facilities_ids:
        if f_id not in facility_capacities:
            raise ValueError(f"Missing capacity for facility '{f_id}'")

    # Variables
    x = {
        (d_id, f_id): model.addVar(vtype="B", name=f"x_{d_id}_{f_id}")
        for d_id in demand_points_ids
        for f_id in facilities_ids
    }
    y = {f_id: model.addVar(vtype="B", name=f"y_{f_id}") for f_id in facilities_ids}

    # Objective
    model.setObjective(
        quicksum(
            cost_matrix[d_id].get(f_id, 1e9) * demand_quantities[d_id] * x[(d_id, f_id)]
            for d_id in demand_points_ids
            for f_id in facilities_ids
        )
        + quicksum(fixcost * y[f_id] for f_id in facilities_ids),
        "minimize",
    )

    # Constraints
    for d_id in demand_points_ids:
        model.addCons(quicksum(x[(d_id, f_id)] for f_id in facilities_ids) == 1)

    for d_id in demand_points_ids:
        for f_id in facilities_ids:
            model.addCons(x[(d_id, f_id)] <= y[f_id])

    for f_id in facilities_ids:
        model.addCons(
            quicksum(
                demand_quantities[d_id] * x[(d_id, f_id)] for d_id in demand_points_ids
            )
            <= facility_capacities[f_id] * y[f_id]
        )

    print("Starting optimization...")
    start_time = time.time()
    model.optimize()
    end_time = time.time()
    solving_time = end_time - start_time

    open_facilities = []
    assignments = {}
    total_assignment_cost = 0
    facility_loads = {f_id: 0.0 for f_id in facilities_ids}

    if model.getStatus() in ["optimal", "timelimit"]:
        print(f"\nStatus: {model.getStatus()} | Objective: {model.getObjVal():.2f}")

        for f_id in facilities_ids:
            if model.getVal(y[f_id]) > 0.5:
                open_facilities.append(f_id)

        for d_id in demand_points_ids:
            for f_id in facilities_ids:
                if model.getVal(x[(d_id, f_id)]) > 0.5:
                    assignments[d_id] = f_id
                    total_assignment_cost += (
                        cost_matrix[d_id].get(f_id, 1e9) * demand_quantities[d_id]
                    )
                    facility_loads[f_id] += demand_quantities[d_id]
                    break

        print(f"Total Assignment Cost: {total_assignment_cost:.2f}")
        print(f"Total Opening Cost: {len(open_facilities) * fixcost:.2f}")
        print(
            f"Total Cost: {total_assignment_cost + len(open_facilities) * fixcost:.2f}"
        )

        # Save assignments
        assignment_results_path = "data/cflp_assignments.json"
        try:
            os.makedirs(os.path.dirname(assignment_results_path), exist_ok=True)
            with open(assignment_results_path, "w") as f:
                json.dump(assignments, f, indent=2, cls=NpEncoder)
            print(f"\nAssignments saved to {assignment_results_path}")
        except Exception as e:
            print(f"Error saving results: {e}")

        return open_facilities, assignments, solving_time
    else:
        print(f"\nModel status: {model.getStatus()}. No feasible solution found.")
        return [], {}, solving_time


def _load_and_handle_gdf(
    project_data_path,
    file_name,
    create_dummy_func,
    data_description,
    berlin_boundary=None,
):
    """
    Helper method to load GeoDataFrames or create dummy data if files are not found.
    Args:
        project_data_path (str): The path to the project's data directory.
        file_name (str): The name of the GeoJSON file.
        create_dummy_func (callable): The function to call to create dummy data.
        data_description (str): A description of the data (e.g., "pharmacies").
        berlin_boundary (gpd.GeoDataFrame, optional): The Berlin boundary for dummy data creation.
    Returns:
        geopandas.GeoDataFrame: The loaded or created GeoDataFrame.
    """
    file_path = os.path.join(project_data_path, file_name)
    gdf = None
    if os.path.exists(file_path):
        try:
            gdf = gpd.read_file(file_path)
            print(f"Loaded {data_description} data from {file_path}")
        except Exception as e:
            print(f"Error loading {data_description} from {file_path}: {e}")
    if gdf is None or gdf.empty:
        print(
            f"No {data_description} data found or failed to load from {file_path}. Creating dummy data..."
        )
        # Pass the berlin_boundary if create_dummy_func expects it
        if "berlin_boundary" in create_dummy_func.__code__.co_varnames:
            gdf = create_dummy_func(berlin_boundary)
        else:
            gdf = create_dummy_func()  # Call without boundary if not needed

        if gdf is not None and not gdf.empty:
            gdf.to_file(file_path, driver="GeoJSON")
            print(f"Dummy {data_description} data saved to {file_path}")
        else:
            print(f"Could not create dummy {data_description} data.")
    return gdf

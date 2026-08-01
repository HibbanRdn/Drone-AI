"""Manual local-coordinate plot polygon editor."""

from __future__ import annotations

from pathlib import Path
from typing import Any, Sequence

import cv2
import numpy as np

from .common import list_to_matrix, polygon_area, polygon_self_intersects, read_json, transform_points, write_json


def empty_plots_payload() -> dict[str, Any]:
    return {
        "type": "FeatureCollection",
        "coordinate_space": "registered_local_pixels",
        "crs": None,
        "manual_status": "pending_user_polygon",
        "boundary_semantics": "manual surveyed analysis area; not automatically assumed to be a complete cadastral plot boundary",
        "features": [],
    }


def ensure_empty_plots(path: Path) -> None:
    if not path.exists():
        write_json(path, empty_plots_payload())


def validate_polygon(vertices: Sequence[Sequence[float]]) -> tuple[bool, str]:
    if len(vertices) < 3:
        return False, "A polygon needs at least three vertices"
    if polygon_area(vertices) <= 1.0:
        return False, "Polygon area is too small"
    if polygon_self_intersects(vertices):
        return False, "Self-intersection is not allowed"
    return True, "valid"


def run_plot_editor(postprocess_run: Path) -> None:
    """Open an interactive Matplotlib editor; save only explicit user polygons."""
    import matplotlib.pyplot as plt
    from matplotlib.widgets import Button, TextBox

    postprocess_run = postprocess_run.resolve()
    mosaic_path = postprocess_run / "survey_mosaic_preview.png"
    transform_path = postprocess_run / "mosaic_transform.json"
    plants_path = postprocess_run / "unique_plants.csv"
    plots_path = postprocess_run / "plots_local.geojson"
    if not mosaic_path.is_file() or not transform_path.is_file():
        raise FileNotFoundError("Run prepare first; mosaic or transform is missing")
    ensure_empty_plots(plots_path)
    payload = read_json(plots_path)
    transform = read_json(transform_path)
    local_to_preview = list_to_matrix(transform["local_to_preview"])
    preview_to_local = list_to_matrix(transform["preview_to_local"])
    image = cv2.cvtColor(cv2.imread(str(mosaic_path)), cv2.COLOR_BGR2RGB)

    fig, axis = plt.subplots(figsize=(15, 8.5))
    plt.subplots_adjust(bottom=0.13)
    axis.imshow(image)
    axis.set_title("Left-click vertices • Enter/double-click finish • Backspace undo • toolbar zoom/pan")
    current: list[list[float]] = []
    artists: list[Any] = []
    status = fig.text(0.02, 0.02, "Ready", color="darkgreen")

    plot_id_axis = fig.add_axes([0.08, 0.055, 0.20, 0.045])
    plot_id_box = TextBox(plot_id_axis, "plot_id", initial=f"plot_{len(payload['features']) + 1:03d}")
    save_axis = fig.add_axes([0.31, 0.055, 0.10, 0.045])
    edit_axis = fig.add_axes([0.43, 0.055, 0.10, 0.045])
    delete_axis = fig.add_axes([0.55, 0.055, 0.12, 0.045])
    undo_axis = fig.add_axes([0.69, 0.055, 0.10, 0.045])
    save_button = Button(save_axis, "Save")
    edit_button = Button(edit_axis, "Edit last")
    delete_button = Button(delete_axis, "Delete last")
    undo_button = Button(undo_axis, "Undo")

    def refresh() -> None:
        nonlocal artists
        for artist in artists:
            artist.remove()
        artists = []
        for feature in payload["features"]:
            local = np.asarray(feature["geometry"]["coordinates"][0][:-1], dtype=np.float64)
            preview = transform_points(local, local_to_preview)
            closed = np.vstack([preview, preview[0]])
            line, = axis.plot(closed[:, 0], closed[:, 1], "-", linewidth=2, label=feature["properties"]["plot_id"])
            artists.append(line)
            label = axis.text(float(preview[:, 0].mean()), float(preview[:, 1].mean()), feature["properties"]["plot_id"], color="white", bbox={"facecolor": "black", "alpha": 0.6})
            artists.append(label)
        if current:
            preview = np.asarray(current)
            line, = axis.plot(preview[:, 0], preview[:, 1], "o--", color="yellow")
            artists.append(line)
        fig.canvas.draw_idle()

    def finish_current() -> None:
        nonlocal current
        local = transform_points(np.asarray(current, dtype=np.float64), preview_to_local) if current else np.empty((0, 2))
        valid, message = validate_polygon(local.tolist())
        if not valid:
            status.set_text(message)
            status.set_color("red")
            fig.canvas.draw_idle()
            return
        plot_id = plot_id_box.text.strip()
        if not plot_id or any(feature["properties"]["plot_id"] == plot_id for feature in payload["features"]):
            status.set_text("plot_id is empty or already exists")
            status.set_color("red")
            return
        ring = local.tolist() + [local[0].tolist()]
        payload["features"].append({"type": "Feature", "geometry": {"type": "Polygon", "coordinates": [ring]}, "properties": {"plot_id": plot_id, "boundary_type": "manual_analysis_area", "coordinate_space": "registered_local_pixels"}})
        payload["manual_status"] = "user_polygons_saved"
        current = []
        plot_id_box.set_val(f"plot_{len(payload['features']) + 1:03d}")
        status.set_text(f"Added {plot_id}; click Save to write GeoJSON")
        status.set_color("darkgreen")
        refresh()

    def on_click(event: Any) -> None:
        if event.inaxes is not axis or event.button != 1 or event.xdata is None or event.ydata is None:
            return
        current.append([float(event.xdata), float(event.ydata)])
        if event.dblclick and len(current) >= 3:
            # Matplotlib reports both clicks; remove a nearly duplicate final vertex.
            if len(current) > 1 and np.linalg.norm(np.asarray(current[-1]) - np.asarray(current[-2])) < 3:
                current.pop()
            finish_current()
        else:
            refresh()

    def on_key(event: Any) -> None:
        nonlocal current
        if event.key == "enter":
            finish_current()
        elif event.key in {"backspace", "ctrl+z"} and current:
            current.pop()
            refresh()
        elif event.key == "escape":
            current = []
            refresh()

    def save(_event: Any) -> None:
        write_json(plots_path, payload)
        status.set_text(f"Saved {len(payload['features'])} polygon(s) to {plots_path.name}")
        status.set_color("darkgreen")
        fig.canvas.draw_idle()

    def delete_last(_event: Any) -> None:
        if payload["features"]:
            removed = payload["features"].pop()["properties"]["plot_id"]
            status.set_text(f"Removed {removed}; click Save to persist")
            if not payload["features"]:
                payload["manual_status"] = "pending_user_polygon"
            refresh()

    def edit_last(_event: Any) -> None:
        nonlocal current
        if current or not payload["features"]:
            status.set_text("Finish/cancel current vertices first, or load a polygon")
            status.set_color("red")
            return
        feature = payload["features"].pop()
        local = np.asarray(feature["geometry"]["coordinates"][0][:-1], dtype=np.float64)
        current = transform_points(local, local_to_preview).tolist()
        plot_id_box.set_val(str(feature["properties"]["plot_id"]))
        status.set_text("Loaded last polygon for vertex editing; Enter finishes it")
        status.set_color("darkgreen")
        refresh()

    def undo(_event: Any) -> None:
        nonlocal current
        if current:
            current.pop()
            refresh()

    fig.canvas.mpl_connect("button_press_event", on_click)
    fig.canvas.mpl_connect("key_press_event", on_key)
    save_button.on_clicked(save)
    edit_button.on_clicked(edit_last)
    delete_button.on_clicked(delete_last)
    undo_button.on_clicked(undo)

    # Plant sample and coverage boundary provide spatial context without overwhelming the GUI.
    if plants_path.is_file():
        import pandas as pd

        plants = pd.read_csv(plants_path)
        sample = plants.iloc[:: max(1, len(plants) // 5000)][["local_x", "local_y"]].to_numpy(dtype=np.float64)
        preview = transform_points(sample, local_to_preview)
        axis.scatter(preview[:, 0], preview[:, 1], s=2, c="lime", alpha=0.45, label="unique plant sample")
    refresh()
    plt.show()

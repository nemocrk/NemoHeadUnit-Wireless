"""
touch_mapper.py — Cross-platform touch coordinate mapping with aspect ratio & margin compensation.

Maps raw screen/widget touch coordinates to Android Auto negotiated video coordinates,
taking into account physical aspect ratio insets (margin_width, margin_height) and
letterboxing / stretch modes.
"""

from typing import NamedTuple, Tuple


class TouchPoint(NamedTuple):
    x: int
    y: int


class TouchCoordinateMapper:
    """
    Normalizes and projects widget touch points to Android Auto coordinate space.
    """

    @staticmethod
    def map_coordinate(
        raw_x: float,
        raw_y: float,
        surface_width: float,
        surface_height: float,
        negotiated_width: int = 1280,
        negotiated_height: int = 720,
        margin_width: int = 0,
        margin_height: int = 0,
        stretch_to_fill: bool = True,
    ) -> TouchPoint:
        surface_w = max(1.0, float(surface_width))
        surface_h = max(1.0, float(surface_height))

        # Active drawing bounds configured on phone
        ui_w = max(1.0, float(negotiated_width - margin_width))
        ui_h = max(1.0, float(negotiated_height - margin_height))

        if stretch_to_fill:
            video_x = (raw_x / surface_w) * ui_w
            video_y = (raw_y / surface_h) * ui_h
        else:
            ui_ratio = ui_w / ui_h
            view_ratio = surface_w / surface_h

            if view_ratio > ui_ratio:
                displayed_w = surface_h * ui_ratio
                displayed_h = surface_h
            else:
                displayed_w = surface_w
                displayed_h = surface_w / ui_ratio

            ui_left = (surface_w - displayed_w) / 2.0
            ui_top = (surface_h - displayed_h) / 2.0

            local_x = raw_x - ui_left
            local_y = raw_y - ui_top

            video_x = (local_x / max(1.0, displayed_w)) * ui_w
            video_y = (local_y / max(1.0, displayed_h)) * ui_h

        clamped_x = int(max(0, min(negotiated_width, round(video_x))))
        clamped_y = int(max(0, min(negotiated_height, round(video_y))))

        return TouchPoint(x=clamped_x, y=clamped_y)

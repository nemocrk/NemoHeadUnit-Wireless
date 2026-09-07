import pytest
from shared.touch_mapper import TouchCoordinateMapper, TouchPoint

pytestmark = pytest.mark.unit


def test_touch_mapper_direct_one_to_one():
    pt = TouchCoordinateMapper.map_coordinate(
        raw_x=100.0,
        raw_y=200.0,
        surface_width=1280.0,
        surface_height=720.0,
        negotiated_width=1280,
        negotiated_height=720,
        stretch_to_fill=True,
    )
    assert pt == TouchPoint(x=100, y=200)


def test_touch_mapper_stretch_scaling():
    pt = TouchCoordinateMapper.map_coordinate(
        raw_x=960.0,
        raw_y=540.0,
        surface_width=1920.0,
        surface_height=1080.0,
        negotiated_width=1280,
        negotiated_height=720,
        stretch_to_fill=True,
    )
    assert pt == TouchPoint(x=640, y=360)


def test_touch_mapper_margins_applied():
    # 1280x720 negotiated with 80px width margin and 40px height margin -> 1200x680 active ui
    pt = TouchCoordinateMapper.map_coordinate(
        raw_x=1920.0,
        raw_y=1080.0,
        surface_width=1920.0,
        surface_height=1080.0,
        negotiated_width=1280,
        negotiated_height=720,
        margin_width=80,
        margin_height=40,
        stretch_to_fill=True,
    )
    assert pt == TouchPoint(x=1200, y=680)


def test_touch_mapper_clamping():
    # Coordinates outside physical display bounds
    pt_negative = TouchCoordinateMapper.map_coordinate(
        raw_x=-50.0,
        raw_y=-100.0,
        surface_width=1000.0,
        surface_height=500.0,
        negotiated_width=800,
        negotiated_height=480,
    )
    assert pt_negative == TouchPoint(x=0, y=0)

    pt_overflow = TouchCoordinateMapper.map_coordinate(
        raw_x=2000.0,
        raw_y=2000.0,
        surface_width=1000.0,
        surface_height=500.0,
        negotiated_width=800,
        negotiated_height=480,
    )
    assert pt_overflow == TouchPoint(x=800, y=480)


def test_touch_mapper_letterboxing_aspect_ratio_preservation():
    # Surface is 1920x1080 (16:9), AA is 800x600 (4:3)
    # view_ratio (1.777) > ui_ratio (1.333) -> pillarboxing with horizontal centering
    pt_center = TouchCoordinateMapper.map_coordinate(
        raw_x=960.0,
        raw_y=540.0,
        surface_width=1920.0,
        surface_height=1080.0,
        negotiated_width=800,
        negotiated_height=600,
        stretch_to_fill=False,
    )
    # Center of screen must map to center of AA frame (400, 300)
    assert pt_center == TouchPoint(x=400, y=300)

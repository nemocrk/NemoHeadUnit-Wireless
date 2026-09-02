"""
test_touch_mapper.py — Verification of aspect ratio and margin touch coordinate mapping.
"""

from shared.touch_mapper import TouchCoordinateMapper


def test_standard_touch():
    # 1:1 mapping on 1280x720
    pt = TouchCoordinateMapper.map_coordinate(
        raw_x=640,
        raw_y=360,
        surface_width=1280,
        surface_height=720,
        negotiated_width=1280,
        negotiated_height=720,
        margin_width=0,
        margin_height=0,
        stretch_to_fill=True
    )
    assert pt.x == 640
    assert pt.y == 360
    print("✅ Standard 1:1 mapping verified: (640, 360)")


def test_bottom_margin_mapping():
    # 1280x720 video with 64px bottom margin rendered on 1280x656 surface
    pt = TouchCoordinateMapper.map_coordinate(
        raw_x=1280,
        raw_y=656,
        surface_width=1280,
        surface_height=656,
        negotiated_width=1280,
        negotiated_height=720,
        margin_width=0,
        margin_height=64,
        stretch_to_fill=True
    )
    assert pt.x == 1280
    assert pt.y == 720 - 64  # 656
    print("✅ Margin height compensation verified: (1280, 656)")


def test_16_10_display_with_horizontal_margin():
    # 1920x1200 display (16:10) with 192px left margin
    pt = TouchCoordinateMapper.map_coordinate(
        raw_x=1920,
        raw_y=1200,
        surface_width=1920,
        surface_height=1200,
        negotiated_width=1920,
        negotiated_height=1080,
        margin_width=192,
        margin_height=0,
        stretch_to_fill=True
    )
    assert pt.x == 1920 - 192  # 1728
    assert pt.y == 1080
    print("✅ 16:10 Horizontal margin mapping verified: (1728, 1080)")


if __name__ == "__main__":
    test_standard_touch()
    test_bottom_margin_mapping()
    test_16_10_display_with_horizontal_margin()
    print("\n🎉 ALL TOUCH MAPPER TESTS PASSED!")

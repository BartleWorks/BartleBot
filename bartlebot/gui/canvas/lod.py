"""Level-of-detail guard for text painted into the graphics scene.

Qt's font engine rasterises each glyph into an offscreen QImage sized from the
painter's device transform.  Once a glyph scales below roughly half a device
pixel that image rounds to 0x0, and QPainter refuses it — four warnings per
distinct glyph, per item, per repaint:

    QPainter::begin: Paint device returned engine == 0, type: 3
    QPainter::setCompositionMode: Painter not active
    QPainter::fillRect: Painter not active
    QPainter::end: Painter not active, aborted

``type: 3`` is ``QInternal::Image`` — the enum is sequential (``Widget=1,
Pixmap=2, Image=3``) — which is what identifies the failing device as the font
engine's glyph buffer rather than anything we paint on directly.

This is the QPainter bug.  It surfaced with the minimap because only the minimap's
fit-to-scene transform is small enough to take labels sub-pixel; the main view
never gets there, which is why the bursts track minimap repaints exactly.  The
text renders to nothing at that size, so the fix is to not ask for it — which
also skips the glyph rasterisation work entirely.
"""

#: Minimum line height, in device pixels, worth asking Qt to render.
#:
#: Failure was measured to start below ~0.5 device pixels of glyph size, which
#: is ~0.7 px of line height.  2.0 keeps a comfortable margin over that while
#: staying far below the point where text carries any information: at this
#: scale a wrapped node label occupies a couple of pixels total.
MIN_TEXT_DEVICE_PX = 2.0


def text_is_legible(font_height: float, lod: float) -> bool:
    """True when text at ``font_height`` scene units is worth painting at ``lod``.

    ``font_height`` is the painter's current ``fontMetrics().height()``, in the
    same coordinate space the text will be drawn in; ``lod`` is the *unclamped*
    ``levelOfDetailFromTransform(painter.worldTransform())``.  Their product is
    the line height in device pixels.

    Pass the unclamped LOD.  An item that clamps its LOD to compensate its font
    size for zoom (``RegionItem``) stops compensating past the clamp, so its
    text shrinks with the real transform from there down — the clamped value
    would report a legible size that is not the one being rendered.
    """
    return font_height * lod >= MIN_TEXT_DEVICE_PX

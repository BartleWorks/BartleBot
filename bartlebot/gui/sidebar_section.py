"""Shared collapsible section widget used by left and right sidebar panels."""

from PyQt6.QtCore import Qt, pyqtSignal
from PyQt6.QtWidgets import QHBoxLayout, QLabel, QSizePolicy, QVBoxLayout, QWidget


class _SectionHeader(QWidget):

    toggled = pyqtSignal(bool)

    def __init__(self, title: str, parent=None):
        super().__init__(parent)

        self._expanded = True

        layout = QHBoxLayout(self)
        layout.setContentsMargins(0, 0, 0, 0)

        self.arrow = QLabel("▼")
        self.arrow.setFixedWidth(12)

        self.label = QLabel(title)
        font = self.label.font()
        font.setBold(True)
        self.label.setFont(font)

        layout.addWidget(self.arrow)
        layout.addWidget(self.label)
        layout.addStretch()

        self.setCursor(Qt.CursorShape.PointingHandCursor)

    def mousePressEvent(self, event):  # noqa: ARG002
        self._expanded = not self._expanded
        self.arrow.setText("▼" if self._expanded else "▶")
        self.toggled.emit(self._expanded)


class SidebarSection(QWidget):
    """A collapsible section with a bold header and a padded content body.

    All sections created with this class share the same content margins,
    ensuring visual consistency across both sidebars without per-section
    margin overrides.

    Usage::

        section = SidebarSection("Options")
        layout = QVBoxLayout()
        layout.addWidget(some_widget)
        section.set_content_layout(layout)
        parent_layout.addWidget(section)
    """

    # Standard padding applied around every section's content area.
    _CONTENT_MARGINS = (0, 2, 0, 6)

    def __init__(self, title: str, parent=None):
        super().__init__(parent)
        self.setSizePolicy(QSizePolicy.Policy.Preferred, QSizePolicy.Policy.Maximum)

        vbox = QVBoxLayout(self)
        vbox.setContentsMargins(0, 0, 0, 0)
        vbox.setSpacing(2)

        self._header = _SectionHeader(title)
        self._body = QWidget()
        self._body.setSizePolicy(QSizePolicy.Policy.Preferred, QSizePolicy.Policy.Maximum)

        vbox.addWidget(self._header)
        vbox.addWidget(self._body)

        self._header.toggled.connect(self._body.setVisible)

    def set_content_layout(self, layout) -> None:
        """Attach *layout* as the section body and apply standard content margins."""
        layout.setContentsMargins(*self._CONTENT_MARGINS)
        self._body.setLayout(layout)

    @property
    def body(self) -> QWidget:
        return self._body

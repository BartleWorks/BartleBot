"""Right sidebar: assembles client info and drop table sections."""

from bartlebot.gui.sidebar.sections.client_section import ClientSection
from bartlebot.gui.sidebar.sections.drop_section import DropSection
from bartlebot.gui.sidebar.sidebar_section import Sidebar


class RightSidebar(Sidebar):
    """Right sidebar containing per-client info and drop tracking sections."""

    def __init__(self, parent=None):
        super().__init__(min_width=300, parent=parent)

        self.clients = ClientSection()
        self.drops = DropSection()

        self._add_section(self.clients)
        self._add_section(self.drops)

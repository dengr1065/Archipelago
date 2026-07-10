from enum import StrEnum

from BaseClasses import Item, ItemClassification

from .songs import OsuSong, get_song_items


class OsuItem(Item):
    game = "osu!"


class OsuItemKind(StrEnum):
    PERFORMANCE_POINTS = "Performance Points"
    CIRCLE = "Circle"

    @property
    def code(self) -> int:
        match self:
            case self.PERFORMANCE_POINTS:
                return 1
            case self.CIRCLE:
                return 2

    @property
    def classification(self) -> ItemClassification:
        match self:
            case self.PERFORMANCE_POINTS:
                return ItemClassification.progression_skip_balancing
            case self.CIRCLE:
                return ItemClassification.filler


def create_non_song_item(name: str, player: int) -> OsuItem | None:
    """
    Creates a non-song `OsuItem`, returning `None` if the passed name does not
    correspond to a non-song item.
    """

    non_song_items = [OsuItemKind.PERFORMANCE_POINTS, OsuItemKind.CIRCLE]

    if name in non_song_items:
        item_kind = OsuItemKind(name)
        return OsuItem(name, item_kind.classification, item_kind.code, player)

    return None


def create_song_item(song: OsuSong, player: int) -> OsuItem:
    """
    Creates an `OsuItem` representing the specified song.
    """

    return OsuItem(song.get_item_name(), ItemClassification.progression, song.id, player)


def get_all_items() -> dict[str, int]:
    """
    Returns a dictionary mapping item names to "codes", unique item IDs. Song
    item IDs match the beatmapset IDs, while very low numbers (maps that are
    not FA) are used by non-song items.
    """

    all_items = get_song_items()
    for item in OsuItemKind:
        all_items[item.value] = item.code

    return all_items

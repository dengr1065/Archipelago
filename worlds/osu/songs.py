import functools
import importlib.resources
import json
from dataclasses import dataclass

MAX_LOCATIONS_PER_SONG = 2


@dataclass
class OsuBeatmap:
    """
    Represents a single beatmap (difficulty) of a beatmapset.
    """

    id: int
    """Unique identifier of this beatmap."""
    mode: str
    """The ruleset this beatmap was made for."""
    sr: float
    """The star rating of this beatmap."""


@dataclass
class OsuSong:
    """
    Represents a beatmapset and its beatmaps (difficulties).
    """

    id: int
    artist: str
    title: str

    status: str
    ranked_year: int
    duration: int
    is_explicit: bool

    beatmaps: list[OsuBeatmap]

    @property
    def is_loved(self):
        return self.status == "loved"

    @property
    def full_name(self):
        return f"{self.artist} - {self.title} [{self.id}]"

    @property
    def display_name(self):
        """
        Returns the song name for display in clients. Unlike `self.full_name`,
        this one uses the song ID as a prefix rather than suffix.
        """

        return f"{self.id}: {self.artist} - {self.title}"

    def get_location_name(self, item_n: int):
        """
        Returns a location name for this song with a suffix indicating the
        location number for this song.
        """

        return f"{self.full_name} (Item {item_n + 1})"

    def get_item_name(self):
        """
        Returns an item name for this song. This is how the song will be
        displayed in games where it can be found.
        """

        return self.full_name

    def get_beatmap(self, beatmap_id: int) -> OsuBeatmap | None:
        return next((beatmap for beatmap in self.beatmaps if beatmap.id == beatmap_id), None)

    def has_mode(self, mode: str):
        return any(beatmap.mode == mode for beatmap in self.beatmaps)


@functools.cache
def get_all_songs() -> dict[int, OsuSong]:
    """
    Returns a dictionary containing all songs supported by this world. Keys
    correspond to beatmapset IDs, values are instances of `OsuSong`.
    """

    from . import data

    data_file = importlib.resources.files(data) / "OsuSongData.json"
    with data_file.open() as f:
        raw_data = json.load(f)

    mapping: dict[int, OsuSong] = {}

    # The data file used to group beatmapsets by Mappers' Guild packs, but this
    # is no longer the case - just get the first "pack"
    for beatmapset in raw_data[0]["beatmapsets"]:
        mapset_id = beatmapset["id"]
        mapping[mapset_id] = OsuSong(
            id=mapset_id,
            artist=beatmapset["artist"],
            title=beatmapset["title"],
            status=beatmapset["status"],
            ranked_year=beatmapset["ranked_date"],
            duration=beatmapset["length"],
            is_explicit=beatmapset["nsfw"],
            beatmaps=[OsuBeatmap(**beatmap) for beatmap in beatmapset["beatmaps"]],
        )

    return mapping


def get_song_locations() -> dict[str, int]:
    """
    Returns a dictionary that links song location names to the "addresses"
    (unique location IDs). Since there can be multiple locations per song,
    the IDs don't map 1:1 to beatmapset IDs.
    """

    locations: dict[str, int] = {}

    for song in get_all_songs().values():
        for n in range(MAX_LOCATIONS_PER_SONG):
            locations[song.get_location_name(n)] = song.id * MAX_LOCATIONS_PER_SONG + n

    return locations


def get_song_items() -> dict[str, int]:
    """
    Returns a dictionary that links song item names to the "codes" (unique item
    IDs). As there is only one item per song, these map directly to beatmapset
    IDs. Very low ID numbers (such as 1 or 10) can be used by other items
    because there are no FA maps in that range.
    """

    return {song.get_item_name(): song.id for song in get_all_songs().values()}

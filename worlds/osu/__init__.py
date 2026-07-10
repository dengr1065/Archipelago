from math import floor

from BaseClasses import Region, Tutorial
from rule_builder.rules import Has

from ..AutoWorld import WebWorld, World
from ..LauncherComponents import Component, Type, components
from .items import OsuItem, OsuItemKind, create_non_song_item, create_song_item, get_all_items
from .locations import OsuLocation
from .options import OsuOptions
from .song_selection import DifficultySync, SongChoice, SongSelectionRules
from .songs import get_all_songs, get_song_locations


def run_client():
    from ..LauncherComponents import launch
    from .client import client

    launch(client.main, "osu!Client")


components.append(Component("osu!Client", func=run_client, component_type=Type.CLIENT))


class OsuWebWorld(WebWorld):
    theme = "partyTime"
    tutorials = [
        Tutorial(
            tutorial_name="WIP",
            description="A guide to playing osu!ap.",
            language="English",
            file_name="guide_en.md",
            link="guide/en",
            authors=["Kanave"],
        )
    ]


class OsuWorld(World):
    """
    osu! is a free to play rhythm game featuring 4 modes, an online ranking system/statistics,
    with user submitted songs downloadable from its website.
    """

    # Lots of code is taken from Mushdash, Clique, and various other APworlds
    game = "osu!"
    options_dataclass = OsuOptions
    options: OsuOptions
    web = OsuWebWorld()

    origin_region_name = "Song Select"
    location_name_to_id = get_song_locations()
    item_name_to_id = get_all_items()
    all_songs = get_all_songs()

    selector: SongSelectionRules
    starting_songs: list[SongChoice]
    additional_songs: list[SongChoice]
    victory_song_id: int
    location_count: int

    def generate_early(self):
        self.selector = SongSelectionRules(self.random)
        self.starting_songs = []
        self.additional_songs = []

        self.selector.starting_songs = self.options.starting_songs.value
        self.selector.additional_songs = self.options.additional_songs.value

        self.selector.duration_filter = (self.options.minimum_length.value, self.options.maximum_length.value)
        self.selector.age_filter = (self.options.maximum_age.value, self.options.minimum_age.value)
        self.selector.allow_explicit = bool(self.options.explicit_lyrics.value)
        self.selector.allow_loved = bool(self.options.enable_loved.value)

        if not self.options.exclude_standard:
            self.selector.range_osu = (
                self.options.minimum_difficulty_standard.value,
                self.options.maximum_difficulty_standard.value,
            )

        if not self.options.exclude_catch:
            self.selector.range_fruits = (
                self.options.minimum_difficulty_catch.value,
                self.options.maximum_difficulty_catch.value,
            )

        if not self.options.exclude_taiko:
            self.selector.range_taiko = (
                self.options.minimum_difficulty_taiko.value,
                self.options.maximum_difficulty_taiko.value,
            )

        if not self.options.exclude_4k:
            self.selector.range_mania_4k = (
                self.options.minimum_difficulty_4k.value,
                self.options.maximum_difficulty_4k.value,
            )

        if not self.options.exclude_7k:
            self.selector.range_mania_7k = (
                self.options.minimum_difficulty_7k.value,
                self.options.maximum_difficulty_7k.value,
            )

        if not self.options.exclude_other_keys:
            self.selector.range_mania_other = (
                self.options.minimum_difficulty_other.value,
                self.options.maximum_difficulty_other.value,
            )

        self.selector.difficulty_sync = DifficultySync(self.options.difficulty_sync.value)

        self.selector.shuffle_included_songs = bool(self.options.shuffle_included_songs.value)
        self.selector.included_songs = {int(song_id) for song_id in self.options.include_songs.value}
        self.selector.excluded_songs = {int(song_id) for song_id in self.options.exclude_songs.value}

        self.selector.verify_options()

        # Song choosing and generation starts here
        self.selector.apply_filters()

        for choice in self.selector.pick_starting_songs():
            self.starting_songs.append(choice)

        for choice in self.selector.pick_additional_songs():
            self.additional_songs.append(choice)

        self.location_count = len(self.starting_songs) + len(self.additional_songs)
        location_multiplier = 1 + (self.get_additional_item_percentage() / 100.0)
        self.location_count = floor(self.location_count * location_multiplier)

        minimum_location_count = len(self.additional_songs) + self.get_music_sheet_count()
        if self.location_count < minimum_location_count:
            self.location_count = minimum_location_count

    def create_regions(self) -> None:
        menu_region = Region(self.origin_region_name, self.player, self.multiworld)
        self.multiworld.regions += [menu_region]

        all_selected_songs = self.starting_songs + self.additional_songs
        two_item_location_count = self.location_count - len(all_selected_songs)

        # Make a region per song/album, then adds 1-2 item locations to them
        for i, choice in enumerate(all_selected_songs):
            region = Region(choice.song.full_name, self.player, self.multiworld)
            self.multiworld.regions.append(region)

            menu_region.connect(region, choice.song.full_name, Has(choice.song.get_item_name()))

            # Up to 2 Locations are defined per song
            n_locations = 2 if i < two_item_location_count else 1
            for location_index in range(n_locations):
                location_name = choice.song.get_location_name(location_index)
                location_address = self.location_name_to_id[location_name]

                location = OsuLocation(self.player, location_name, location_address, region)
                region.locations.append(location)

        # Pick a random song to be the victory one, then create an event
        victory_song = self.random.choice(all_selected_songs).song
        victory_region = self.get_region(victory_song.full_name)

        has_victory_song = Has(victory_song.get_item_name())
        can_play_victory_song = Has(OsuItemKind.PERFORMANCE_POINTS, self.get_music_sheet_win_count())
        victory_region.add_event(
            victory_song.full_name,
            "Victory",
            has_victory_song & can_play_victory_song,
            location_type=OsuLocation,
            item_type=OsuItem,
        )

        # Make sure the client knows which song is the victory one
        self.victory_song_id = victory_song.id

    def create_items(self) -> None:
        # Note: Item count will be off if plando is involved.
        item_count = self.get_music_sheet_count()

        # First add all goal song tokens
        for _ in range(item_count):
            self.multiworld.itempool.append(self.create_item(OsuItemKind.PERFORMANCE_POINTS))

        # Precollect starting songs
        for choice in self.starting_songs:
            item = self.create_item(choice.song.get_item_name())
            self.push_precollected(item)

        # Add rest of the songs to the item pool
        for choice in self.additional_songs:
            item_name = choice.song.get_item_name()
            self.multiworld.itempool.append(self.create_item(item_name))

        item_count += len(self.additional_songs)

        # Next fill all remaining slots with filler items
        items_needed = self.location_count - item_count
        if items_needed:
            for _ in range(0, self.location_count - item_count):
                self.multiworld.itempool.append(self.create_filler())

    def create_item(self, name: str) -> OsuItem:
        # Assume this is not a song item
        item = create_non_song_item(name, self.player)
        if item:
            return item

        # If it is, resolve the song and create its item
        song_id = self.item_name_to_id[name]
        song = self.all_songs[song_id]

        return create_song_item(song, self.player)

    def get_filler_item_name(self) -> str:
        return OsuItemKind.CIRCLE

    def set_rules(self) -> None:
        self.set_completion_rule(Has("Victory"))

    def get_music_sheet_count(self) -> int:
        multiplier = self.options.performance_points_count_percentage / 100.0
        song_count = (len(self.starting_songs) * 2) + len(self.additional_songs)
        return max(1, floor(song_count * multiplier))

    def get_music_sheet_win_count(self) -> int:
        multiplier = self.options.performance_points_win_count_percentage.value / 100.0
        sheet_count = self.get_music_sheet_count()
        return max(1, floor(sheet_count * multiplier))

    def get_additional_item_percentage(self) -> int:
        return self.options.additional_item_percentage.value

    def fill_slot_data(self):
        # The client needs to know which difficulties can be played when
        # difficulty sync is enabled
        selected_songs = self.starting_songs + self.additional_songs
        difficulties = {choice.song.id: choice.difficulty_ids for choice in selected_songs}

        return {
            "PermittedBeatmaps": difficulties,
            "PreformancePointsNeeded": self.get_music_sheet_win_count(),
            "VictorySong": self.victory_song_id,
            "DisableDifficultyReduction": self.options.disable_difficulty_reduction.value,
            "DifficultySync": self.options.difficulty_sync.value,
            "DisallowConverts": self.options.disallow_converts.value,
            "MinimumGrade": self.options.minimum_grade.value,
            "VersionNumber": "1.1b",
        }

import logging
from collections.abc import Iterator
from dataclasses import dataclass
from enum import IntEnum
from random import Random

from Options import OptionError

from .songs import OsuBeatmap, OsuSong, get_all_songs

MAX_STARTING_SONGS = 10
MIN_ADDITIONAL_SONGS = 15
MAX_ADDITIONAL_SONGS = 400
MAX_STAR_RATING = 1000


class DifficultySync(IntEnum):
    OFF = 0
    """Passing any difficulty of a beatmap will collect its locations"""
    STRICT_ANY = 1
    """Only difficulties in the specified Star Rating range are considered valid"""
    STRICT_RANDOM = 2
    """A random difficulty has to be passed in order to collect song locations"""


@dataclass
class SongChoice:
    song: OsuSong
    difficulties: list[OsuBeatmap]
    """
    List of beatmaps that are eligible for collecting this song. Can be ignored
    by the client if difficulty sync is turned off.
    """

    @property
    def difficulty_ids(self):
        return [difficulty.id for difficulty in self.difficulties]


class SongSelectionRules:
    random: Random
    pool: list[int]

    starting_songs: int
    additional_songs: int

    duration_filter: tuple[int, int]
    age_filter: tuple[int, int]
    allow_explicit: bool
    allow_loved: bool

    range_osu: tuple[int, int] | None = None
    range_fruits: tuple[int, int] | None = None
    range_taiko: tuple[int, int] | None = None
    range_mania_4k: tuple[int, int] | None = None
    range_mania_7k: tuple[int, int] | None = None
    range_mania_other: tuple[int, int] | None = None

    difficulty_sync: DifficultySync

    shuffle_included_songs: bool
    included_songs: set[int]
    excluded_songs: set[int]

    def __init__(self, random: Random):
        self.random = random
        self.pool = list(get_all_songs().keys())

        random.shuffle(self.pool)

    @property
    def total_count(self):
        """
        Retrieves the target song count, including starting and user-specified
        songs. Actual number of picked songs may not always reach this value.
        """

        return self.starting_songs + len(self.included_songs) + self.additional_songs

    def verify_options(self):
        """
        Raises an error if inconsistency is detected in properties set on the
        instance. These checks are not exhaustive and are only intended for
        development-time validation.
        """

        # Usually the largest source of confusion.
        # Note: max_age goes first in the tuple
        max_age, min_age = self.age_filter
        if max_age > min_age:
            raise OptionError(f"maximum_age ({max_age}) must not be greater than minimum_age ({min_age})")

        min_duration, max_duration = self.duration_filter
        if min_duration > max_duration:
            raise OptionError(
                f"minimum_length ({min_duration}) must not be greater than maximum_length ({max_duration})"
            )

        if self.starting_songs < 1:
            raise OptionError("Unable to start with less than one song available")
        if self.starting_songs > MAX_STARTING_SONGS:
            raise OptionError(f"Too many starting songs (max = {MAX_STARTING_SONGS})")

        if self.additional_songs < MIN_ADDITIONAL_SONGS:
            raise OptionError(f"Too few additional songs (min = {MIN_ADDITIONAL_SONGS})")
        if self.additional_songs > MAX_ADDITIONAL_SONGS:
            raise OptionError(f"Too many additional songs (max = {MAX_ADDITIONAL_SONGS})")

        if (
            (not self.range_osu)
            and (not self.range_fruits)
            and (not self.range_taiko)
            and (not self.range_mania_4k)
            and (not self.range_mania_7k)
            and (not self.range_mania_other)
        ):
            raise OptionError("All play modes have been excluded, no songs can be picked")

        self.range_osu and self.verify_sr_range(self.range_osu, "osu!")
        self.range_fruits and self.verify_sr_range(self.range_fruits, "osu!catch")
        self.range_taiko and self.verify_sr_range(self.range_taiko, "osu!taiko")
        self.range_mania_4k and self.verify_sr_range(self.range_mania_4k, "osu!mania (4K)")
        self.range_mania_7k and self.verify_sr_range(self.range_mania_7k, "osu!mania (7K)")
        self.range_mania_other and self.verify_sr_range(self.range_mania_other, "osu!mania (Other)")

    def verify_sr_range(self, sr_range: tuple[int, int], mode: str):
        """
        See docstring for `verify_options`.
        """

        min_sr, max_sr = sr_range

        if min_sr < 0 or max_sr < 0:
            raise OptionError(f"Star rating of mode {mode} cannot be lower than zero")
        if min_sr > MAX_STAR_RATING or max_sr > MAX_STAR_RATING:
            raise OptionError(f"Star rating of mode {mode} cannot be greater than {MAX_STAR_RATING}")

        if min_sr > max_sr:
            raise OptionError(f"Maximum star rating of mode {mode} ({max_sr}) cannot exceed the minimum ({min_sr})")

    def apply_filters(self):
        """
        Removes filtered out and excluded songs from the pool. Does not apply
        difficulty-level filters.
        """

        # Also remove included songs from the pool as they are picked directly
        # from the included_songs set
        to_remove = set(self.included_songs.union(self.excluded_songs))

        min_duration, max_duration = self.duration_filter
        max_age, min_age = self.age_filter

        for song in get_all_songs().values():
            if song.is_explicit and (not self.allow_explicit):
                to_remove.add(song.id)
                continue

            if song.is_loved and (not self.allow_loved):
                to_remove.add(song.id)
                continue

            if not (min_duration <= song.duration <= max_duration):
                to_remove.add(song.id)
                continue

            if not (max_age <= song.ranked_year <= min_age):
                to_remove.add(song.id)
                continue

        for song_id in to_remove:
            self.pool.remove(song_id)

    def pick_starting_songs(self) -> Iterator[SongChoice]:
        """
        Picks the starting songs, considering users' choice of included songs
        and removing the songs from randomization pool.
        """

        # First, try to add randomized starting songs
        songs_added = 0
        for choice in self.pick_random_songs():
            yield choice

            songs_added += 1
            if songs_added == self.starting_songs:
                break

        if songs_added < self.starting_songs:
            raise ValueError(f"Failed to find enough songs to provide {self.starting_songs} starting items")

        # Now, if included songs should not be randomized, add those in order
        if not self.shuffle_included_songs:
            yield from self.pick_included_songs()

    def pick_additional_songs(self) -> Iterator[SongChoice]:
        if self.shuffle_included_songs:
            yield from self.pick_included_songs()

        songs_added = 0
        for choice in self.pick_random_songs():
            yield choice

            songs_added += 1
            if songs_added == self.additional_songs:
                break

        if songs_added < MIN_ADDITIONAL_SONGS:
            raise ValueError(f"Not enough songs in pool to ensure {self.additional_songs} additional songs")
        if songs_added < self.additional_songs:
            logging.warning(f"Unable to generate {self.additional_songs} additional songs, only adding {songs_added}")
            self.additional_songs = songs_added

    def pick_random_songs(self):
        """
        Chooses difficulties for random songs, removing them from the pool.
        Songs with all difficulties filtered out will be skipped.
        """

        all_songs = get_all_songs()
        while self.pool:
            song = all_songs[self.pool.pop(0)]
            difficulties = self.find_difficulties(song, False)

            if difficulties:
                yield SongChoice(song, difficulties)

    def pick_included_songs(self):
        """
        Chooses difficulties for all included songs, not removing them from the
        pool. If no suitable difficulties can be found, all difficulties will
        be allowed.
        """

        all_songs = get_all_songs()
        for song_id in sorted(self.included_songs):
            song = all_songs[song_id]
            difficulties = self.find_difficulties(song, True)
            yield SongChoice(song, difficulties)

    def find_difficulties(self, song: OsuSong, fallback: bool) -> list[OsuBeatmap]:
        """
        Attempts to find difficulties of the provided song that may be played
        according to the options set on this `SongSelectionRules`. This
        includes handling difficulty sync when set to
        `DifficultySync.STRICT_RANDOM`.
        """
        found_difficulties: list[OsuBeatmap] = []

        for difficulty in song.beatmaps:
            sr_range = self.get_sr_range_for_mode(difficulty.mode)
            if not sr_range:
                # This ruleset is excluded
                continue

            if sr_range[0] <= (difficulty.sr * 100) <= sr_range[1]:
                found_difficulties.append(difficulty)

        if found_difficulties and self.difficulty_sync == DifficultySync.STRICT_RANDOM:
            found_difficulties = [self.random.choice(found_difficulties)]

        if fallback and not found_difficulties:
            # Allow all difficulties to be played as a last resort
            found_difficulties = list(song.beatmaps)

        return found_difficulties

    def get_sr_range_for_mode(self, mode: str) -> tuple[int, int] | None:
        match mode:
            case "osu":
                return self.range_osu
            case "fruits":
                return self.range_fruits
            case "taiko":
                return self.range_taiko
            case "4k":
                return self.range_mania_4k
            case "7k":
                return self.range_mania_7k
            case "other":
                return self.range_mania_other

        raise ValueError(f'Unknown mode "{mode}"')

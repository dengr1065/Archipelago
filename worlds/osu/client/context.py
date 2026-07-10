from __future__ import annotations

import ast
import asyncio
import os
import ssl
import time
import webbrowser

import aiohttp

import Utils
from CommonClient import ClientCommandProcessor, CommonContext

from .. import songs
from ..items import OsuItemKind
from ..songs import OsuSong
from . import api

try:
    import certifi
except ImportError:
    certifi = None

REQUEST_SSL = ssl.create_default_context(cafile=certifi.where()) if certifi else True


class OsuContext(CommonContext):
    game = "osu!"
    items_handling = 0b111  # full remote

    command_processor: type[OsuCommandProcessor]

    def __init__(self, server_address, password):
        super().__init__(server_address, password)
        self.command_processor = OsuCommandProcessor

        self.songs: dict[int, OsuSong] = {}
        self.song_locations = songs.get_song_locations()

        self.permitted_difficulties: dict[int, list[int]] = {}
        self.performance_points_needed = 9999
        self.victory_song_id = -1

        self.send_index: int = 0
        self.last_scores: list = []
        self.auto_modes: list[str] = []
        self.auto_download: bool = False
        self.download_type: str = "direct"
        self.token: str = ""
        self.disable_difficulty_reduction: bool = False
        self.all_locations: list[int] = []
        self.difficulty_sync = 0
        self.minimum_grade = 0
        self.disallow_converts = False

        http_connector = aiohttp.TCPConnector(ssl=REQUEST_SSL)
        self.http_session = aiohttp.ClientSession(connector=http_connector)

        self.config_path = self.ensure_path("config")
        self.settings_path = self.ensure_path("settings")

    async def shutdown(self):
        await super().shutdown()
        await self.http_session.close()

    def get_playable_songs(self, mode: str | None = None) -> list[OsuSong]:
        playable_songs = [
            song for song in self.songs.values() if self.is_song_unlocked(song) and not self.is_song_completed(song)
        ]

        # A victory song isn't always playable even if unlocked
        victory_song = self.songs[self.victory_song_id]
        if victory_song in playable_songs and not self.can_play_victory_song():
            playable_songs.remove(victory_song)

        # Filter by mode if specified
        if mode:

            def filter_func(song: OsuSong) -> bool:
                return song.has_mode(mode)

            playable_songs = list(filter(filter_func, playable_songs))

        return playable_songs

    def get_played_songs(self) -> list[OsuSong]:
        return [song for song in self.songs.values() if self.is_song_completed(song)]

    def is_song_unlocked(self, song: OsuSong) -> bool:
        return any(item.item == song.id for item in self.items_received)

    def is_song_completed(self, song: OsuSong) -> bool:
        song_location = self.song_locations[song.get_location_name(0)]
        return song_location in self.checked_locations

    def can_play_victory_song(self) -> bool:
        received_ids = [item.item for item in self.items_received]
        if self.victory_song_id not in received_ids:
            return False

        return self.get_performance_points() > self.performance_points_needed

    def is_difficulty_allowed(self, song: OsuSong, difficulty: int):
        if not self.difficulty_sync:
            return True

        beatmap = song.get_beatmap(difficulty)
        if not beatmap:
            return False

        return beatmap.id in self.permitted_difficulties[song.id]

    def get_performance_points(self) -> int:
        return [item.item for item in self.items_received].count(OsuItemKind.PERFORMANCE_POINTS.code)

    def ensure_path(self, *segments: str):
        """
        Resolves a file path in the Archipelago user data directory (guaranteed
        to be writable), recursively creating any missing intermediate
        directories if needed.
        """
        target_dir = Utils.user_path("APosu", *segments[:-1])
        os.makedirs(target_dir, exist_ok=True)

        return os.path.join(target_dir, segments[-1])

    async def server_auth(self, password_requested: bool = False):
        await super().server_auth(password_requested)
        await self.get_username()
        await self.send_connect()

    def on_package(self, cmd: str, args: dict):
        if cmd in {"Connected"}:
            print(args)
            slot_data = args.get("slot_data", None)
            if slot_data:
                # When getting slot data from the server, keys of dictionaries
                # are always strings, convert all string keys to integers for
                # easier lookup
                permitted_beatmaps: dict[str, list[int]] = slot_data.get("PermittedBeatmaps", {})
                self.permitted_difficulties = {int(key): value for key, value in permitted_beatmaps.items()}

                self.performance_points_needed = slot_data.get("PreformancePointsNeeded", 9999)
                self.victory_song_id = slot_data.get("VictorySong", -1)
                self.disable_difficulty_reduction = slot_data.get("DisableDifficultyReduction", False)
                self.difficulty_sync = slot_data.get("DifficultySync", 0)
                self.minimum_grade = slot_data.get("MinimumGrade", 0)
                self.disallow_converts = slot_data.get("DisallowConverts", False)
                version = slot_data.get("VersionNumber", None)
                if version is None:
                    pass

            # Update the dictionary of songs in this world
            known_songs = songs.get_all_songs()
            self.songs = {song_id: known_songs[song_id] for song_id in self.permitted_difficulties}

    def run_gui(self):
        """Import kivy UI system and start running it as self.ui_task."""
        from kvui import GameManager

        class OsuManager(GameManager):
            logging_pairs = [("Client", "Archipelago")]
            base_title = "Archipelago osu! Client"

        self.ui = OsuManager(self)
        self.ui_task = asyncio.create_task(self.ui.async_run(), name="UI")

    async def get_token(self, output_function=print):
        try:
            async with self.http_session.post(
                "https://osu.ppy.sh/oauth/token",
                headers={"Accept": "application/json", "Content-Type": "application/x-www-form-urlencoded"},
                data=f"client_id={os.environ['CLIENT_ID']}&client_secret={os.environ['API_KEY']}"
                f"&grant_type=client_credentials&scope=public",
            ) as authreq:
                tokenjson = await authreq.json()
                print(tokenjson)
                self.token = tokenjson["access_token"]
                return tokenjson["access_token"]
        except KeyError:
            output_function("Error accessing osu! servers. Check your Client id, Client Secret, and Player id.")


class OsuCommandProcessor(ClientCommandProcessor):
    ctx: OsuContext

    def __init__(self, ctx: OsuContext):
        super().__init__(ctx)
        self.mode_names = {
            "fruits": "fruits",
            "catch": "fruits",
            "ctb": "fruits",
            "4k": "mania",
            "7k": "mania",
            "o!m": "mania",
            "mania": "mania",
            "osu": "osu",
            "std": "osu",
            "standard": "osu",
            "taiko": "taiko",
            "": "",
        }
        self.download_types = {"mirror": "mirror", "direct": "direct"}

    def _cmd_set_api_key(self, key=""):
        """Sets the Client Secret, generated in the "OAuth" Section of Account Settings"""
        os.environ["API_KEY"] = key
        self.output(f"Set to ##################")

    def _cmd_set_client_secret(self, key=""):
        """Sets the Client Secret, generated in the "OAuth" Section of Account Settings"""
        os.environ["API_KEY"] = key
        self.output(f"Set to ##################")

    def _cmd_set_client_id(self, client_id=""):
        """Sets the Client ID, generated in the "OAuth" Section of Account Settings"""
        os.environ["CLIENT_ID"] = client_id
        self.output(f"Set to {client_id}")

    def _cmd_set_player_id(self, player_id=""):
        """Sets the player's user ID, found in the URL of their profile"""
        os.environ["PLAYER_ID"] = player_id
        self.output(f"Set to {player_id}")

    def _cmd_save_keys(self):
        """Saves the player's current IDs"""
        with open(self.ctx.config_path, "w") as f:
            for info in [os.environ["API_KEY"], os.environ["CLIENT_ID"], os.environ["PLAYER_ID"]]:
                f.write(info)
                f.write(" ")
        self.output("Saved Current Keys")

    def _cmd_load_keys(self):
        """loads the player's previously saved IDs"""
        with open(self.ctx.config_path, "r") as f:
            data = f.read()
        d = data.split(" ")
        os.environ["API_KEY"], os.environ["CLIENT_ID"], os.environ["PLAYER_ID"] = (
            d[0],
            d[1],
            d[2],
        )
        self.output("Loaded Previous Keys")

    def _cmd_save_settings(self):
        """Saves the player's current settings. Doesn't include API keys or IDs."""
        with open(self.ctx.settings_path, "w") as f:
            for info in [self.ctx.auto_modes, self.ctx.auto_download, self.ctx.download_type]:
                f.write(str(info))
                f.write("\t")
        self.output("Saved Auto Tracking, Auto Download, and Download Type Settings.")

    def _cmd_load_settings(self):
        """Loads the player's previously saved settings. Doesn't include API keys or IDs."""
        with open(self.ctx.settings_path, "r") as f:
            data = f.read()
        d = data.split("\t")
        self.ctx.auto_modes, self.ctx.auto_download, self.ctx.download_type = (
            ast.literal_eval(d[0]),
            ast.literal_eval(d[1]),
            d[2],
        )
        self.output("Loaded Previous Settings")

    def _cmd_save_all(self):
        """Saves both the player's current IDs, and their settings."""
        with open(self.ctx.config_path, "w") as f:
            for info in [os.environ["API_KEY"], os.environ["CLIENT_ID"], os.environ["PLAYER_ID"]]:
                f.write(info)
                f.write(" ")
        self.output("Saved Current Keys")
        with open(self.ctx.settings_path, "w") as f:
            for info in [self.ctx.auto_modes, self.ctx.auto_download, self.ctx.download_type]:
                f.write(str(info))
                f.write("\t")
        self.output("Saved Auto Tracking, Auto Download, and Download Type Settings.")

    def _cmd_load_all(self):
        """loads the player's previously saved IDs, and their settings."""
        with open(self.ctx.config_path, "r") as f:
            data = f.read()
            d = data.split(" ")
            os.environ["API_KEY"], os.environ["CLIENT_ID"], os.environ["PLAYER_ID"] = (
                d[0],
                d[1],
                d[2],
            )
            self.output("Loaded Previous Keys")
        with open(self.ctx.settings_path, "r") as f:
            data = f.read()
        d = data.split("\t")
        self.ctx.auto_modes, self.ctx.auto_download, self.ctx.download_type = (
            ast.literal_eval(d[0]),
            ast.literal_eval(d[1]),
            d[2],
        )
        self.output("Loaded Previous Settings")

    def _cmd_songs(self, mode=""):
        """Display all songs in logic. Opionally filter for a mode"""
        if mode and mode.lower() in self.mode_names.keys():
            mode = self.mode_names[mode.lower()]

        available_songs = self.ctx.get_playable_songs(mode or None)
        self.output(
            f"You Have {self.ctx.get_performance_points()} Performance Points, "
            f"you need {self.ctx.performance_points_needed} to unlock your goal."
        )
        self.output(f"You currently have {len(available_songs)} {mode + ' ' if mode else ''}songs in Logic")

        for song in available_songs:
            if song.id == self.ctx.victory_song_id:
                self.output(f"{song.display_name} [Victory]")
            else:
                self.output(song.display_name)

    def _cmd_all_songs(self):
        """Displays all songs included in current generation."""
        played_songs = self.ctx.get_played_songs()
        self.output(f"You have played {len(played_songs)}/{len(self.ctx.songs)} songs")

        for song in self.ctx.songs.values():
            if song in played_songs:
                self.output(f"{song.display_name} [passed]")
            else:
                self.output(song.display_name)

    def _cmd_update(self, mode=""):
        """Gets the player's last score, in a given gamemode or their set default"""
        if mode:
            if mode.lower() in self.mode_names.keys():
                mode = self.mode_names[mode.lower()]
            else:
                self.output("Please input a valid mode. Valid modes are Standard, Catch, Taiko, and Mania.")
                return
        asyncio.create_task(api.get_last_scores(self.ctx, mode, self.output))

    async def _cmd_download(self, number=""):
        """Downloads the given song number in '/songs'. Also Accepts "Next", "Victory", and any mode."""
        if number.lower() == "next":
            await api.download_next_beatmapset(self.ctx, None, self.output)
            return

        if number.lower() in self.mode_names.keys():
            mode = self.mode_names[number.lower()]
            in_logic = self.ctx.get_playable_songs(mode)
            if in_logic:
                await api.download_beatmapset(self.ctx, in_logic[0], self.output)
            else:
                self.output(f"You have no {mode} songs to download")
            return

        if number.lower() == "victory":
            await api.download_beatmapset(self.ctx, self.ctx.songs[self.ctx.victory_song_id], self.output)
            return

        try:
            beatmapset = self.ctx.songs[int(number)]
            await api.download_beatmapset(self.ctx, beatmapset, self.output)
        except ValueError:
            self.output("Please Give a Number, 'next' or 'Victory'")
        except IndexError:
            self.output("Use the Song Numbers in '/songs' (Not the IDs)")

    def _cmd_auto_track(self, mode=""):
        """Toggles Auto Tracking for the Given Mode (or "All"). Supports Multiple Modes."""
        try:
            [os.environ["API_KEY"], os.environ["CLIENT_ID"], os.environ["PLAYER_ID"]]
        except KeyError:
            self.output("Please set your Client ID, Client Secret, and Player ID")
            return
        if not self.ctx.token:
            asyncio.create_task(self.ctx.get_token(self.output))
        if mode.lower() == "all":
            self.ctx.auto_modes = ["osu", "fruits", "taiko", "mania"]
            self.output("Auto Tracking Enabled for all modes")
            return
        if mode.lower() in self.mode_names.keys():
            if self.mode_names[mode.lower()] not in self.ctx.auto_modes:
                self.ctx.auto_modes.append(self.mode_names[mode.lower()])
                self.output(f"Auto Tracking Enabled{f' for {mode}' if mode else ' for your default mode'}")
                return
            self.output(f"Auto Tracking Disabled{f' for {mode}' if mode else ' for your default mode'}")
            self.ctx.auto_modes.remove(self.mode_names[mode.lower()])
            return
        self.output("Please Supply a Valid Mode")

    def _cmd_auto_download(self):
        """Toggles Auto Downloads when Auto Tracking"""
        if not self.ctx.token:
            asyncio.create_task(self.ctx.get_token(self.output))
        self.ctx.auto_download = not self.ctx.auto_download
        if self.ctx.auto_download:
            self.output("Toggled Auto Downloading On")
            return
        self.output("Toggled Auto Downloading Off")

    def _cmd_download_type(self, download_type=""):
        """Sets Download type. Valid Options are 'Direct' and 'Mirror'"""
        try:
            [os.environ["API_KEY"], os.environ["CLIENT_ID"]]
        except KeyError:
            self.output("Please set your Client ID, and Client Secret")
            return
        if download_type.lower() in self.download_types:
            self.ctx.download_type = self.download_types[download_type.lower()]
            self.output(f'Download type set to "{self.ctx.download_type.capitalize()}"')
            return
        self.output('Please Use Either "Direct" or "Mirror"')

    def _cmd_check_diff(self, number=""):
        """Outputs the difficulties of a given song ID that are in logic. Only Applies for Difficulty Sync."""
        try:
            [os.environ["API_KEY"], os.environ["CLIENT_ID"]]
        except KeyError:
            self.output("Please set your Client ID, and Client Secret")
            return

        if not self.ctx.difficulty_sync:
            self.output("You aren't using difficulty sync.")
            return

        if number.lower() == "next":
            available_songs = self.ctx.get_playable_songs()
            if not available_songs:
                self.output("You have no songs in logic")
                return

            song = available_songs[0]
        elif number.lower() == "victory":
            song = self.ctx.songs[self.ctx.victory_song_id]
        else:
            try:
                song = self.ctx.songs[int(number)]
            except IndexError:
                self.output("Song with the specified ID was not found")
                return
            except ValueError:
                self.output("Please Give a Number, 'next' or 'Victory'")
                return

        asyncio.create_task(self.get_diff_name(song))

    async def get_diff_name(self, song: OsuSong):
        if not self.ctx.token:
            await self.ctx.get_token(self.output)
        url = f"https://osu.ppy.sh/api/v2/beatmapsets/{song.id}"
        headers = {
            "Accept": "application/json",
            "Content-Type": "application/json",
            "Authorization": f"Bearer {self.ctx.token}",
        }
        async with self.ctx.http_session.get(url, headers=headers) as request:
            beatmapset = await request.json()
        for i in beatmapset["beatmaps"]:
            if any(i["id"] == beatmap.id for beatmap in song.beatmaps):
                self.output(f"{i['version']} - {i['difficulty_rating']}*")

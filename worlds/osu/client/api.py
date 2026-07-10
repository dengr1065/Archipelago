import asyncio
import os
import time
import subprocess
import webbrowser

import Utils
from NetUtils import ClientStatus

from ..songs import OsuSong, OsuBeatmap
from .context import OsuContext


def open_direct_url(url: str, output_function=print):
    if Utils.is_linux:
        try:
            subprocess.Popen(["xdg-open", url], close_fds=True, start_new_session=True)
        except OSError:
            output_function(f"Failed to open osu!direct URL {url}")
        return

    webbrowser.open(url)


async def open_set_in_direct(ctx: OsuContext, diff_id: int, fallback: bool = False, output_function=print):
    # If the beatmapset has no difficulty ID, we have to fall back to the beatmap ID as done in previous versions
    if fallback:
        if not ctx.token:
            await ctx.get_token(output_function)
        url = f"https://osu.ppy.sh/api/v2/beatmapsets/{diff_id}"
        headers = {
            "Accept": "application/json",
            "Content-Type": "application/json",
            "Authorization": f"Bearer {ctx.token}",
        }
        async with ctx.http_session.get(url, headers=headers) as conversion:
            beatmapset = await conversion.json()
            output_function(beatmapset)
        open_direct_url(f"osu://b/{beatmapset['beatmaps'][0]['id']}")
        return

    # otherwise we can just open the first diff directly
    open_direct_url(f"osu://b/{diff_id}")


def get_best_difficulty(ctx: OsuContext, beatmapset: OsuSong) -> OsuBeatmap:
    preferred_difficulty_id = ctx.permitted_difficulties[beatmapset.id][0]
    for beatmap in beatmapset.beatmaps:
        if beatmap.id == preferred_difficulty_id:
            return beatmap

    return beatmapset.beatmaps[0]


async def download_beatmapset(ctx: OsuContext, beatmapset: OsuSong, output_function=print):
    if ctx.download_type == "direct":
        # Don't download the beatmap, just open it instead
        difficulty = get_best_difficulty(ctx, beatmapset)
        if difficulty:
            return await open_set_in_direct(ctx, difficulty.id, False, output_function)

        return await open_set_in_direct(ctx, beatmapset.id, True, output_function)

    output_function(f"Downloading {beatmapset.full_name}")
    try:
        async with ctx.http_session.get(f"https://beatconnect.io/b/{beatmapset.id}") as req:
            content_length = req.headers.get("Content-Length")
            req_status = req.status
            if req_status != 200:
                # The library doesn't have a built-in way to get the status name in our version of aiohttp
                # I have only included the most likely status codes to be returned by beatconnect
                http_status_names = {
                    400: "Bad Request",
                    401: "Unauthorized",
                    403: "Forbidden",
                    404: "Not Found",
                    408: "Request Timeout",
                    429: "Too Many Requests",
                    500: "Internal Server Error",
                    502: "Bad Gateway",
                    503: "Service Unavailable",
                    504: "Gateway Timeout",
                }
                output_function(f"Error Downloading {beatmapset.id} {beatmapset.artist} - {beatmapset.title}.osz")
                output_function(
                    f"Please Manually Add the Map or Try Again Later. ({req_status} - {http_status_names.get(req_status, 'Unknown Error')})"
                )
                return
            # With beatconnect we always know the total size of the download, so this is always true
            if content_length is not None:
                total_bytes = int(content_length)
                total_mb = total_bytes / (1024**2)
                # Beatconnect is slow to respond, so this message will appear when the download starts
                output_function(f"Starting download of beatmapset ({total_mb:.2f}MB)")

            downloaded_content = []
            downloaded_bytes = 0
            last_print_time = time.time()
            async for chunk in req.content.iter_any():
                downloaded_content.append(chunk)
                downloaded_bytes += len(chunk)
                downloaded_mb = downloaded_bytes / (1024**2)

                # If we know the total size, calculate the progress
                if content_length is not None:
                    progress = min(100, int(downloaded_bytes / total_bytes * 100))

                    # Check if at least half a second has passed since last print or if the download is done
                    # Filesizes are small enough that we can do half a second intervals instead of 1 second
                    current_time = time.time()
                    if current_time - last_print_time >= 0.5 or downloaded_bytes == total_bytes:
                        output_function(f"Downloaded: {downloaded_mb:.2f}MB / {total_mb:.2f}MB ({progress}%)")
                        last_print_time = current_time

                # If we don't know the total size, just print the downloaded amount
                else:
                    output_function(f"Downloaded: {downloaded_mb:.2f}MB")

            # Combine all the chunks into one just like req.read() would do
            content = b"".join(downloaded_content)
        f = f"{beatmapset.id} {beatmapset.artist} - {beatmapset.title}.osz"
        filename = "".join(i for i in f if i not in '\\/:*?<>|"')

        file_path = ctx.ensure_path(filename)
        with open(file_path, "wb") as f:
            f.write(content)

        output_function(f"Opening {filename}...")  # More feedback to the user
        webbrowser.open(file_path)
    except Exception as e:
        output_function(f"An error occurred: {repr(e)}")


async def download_next_beatmapset(ctx: OsuContext, task, output_function=print):
    if task:
        await task
        await asyncio.sleep(1)  # Delay to get the reply

    available_songs = ctx.get_playable_songs()
    if not available_songs:
        return

    await download_beatmapset(ctx, available_songs[0], output_function)


def check_location(ctx: OsuContext, score, output_function):
    output_function(score["beatmapset"]["title"] + " " + score["beatmap"]["version"] + f" Passed: {score['passed']}")
    # Check if the score is a pass, then check if it's in the AP
    if not score["passed"]:
        return
    if ctx.disable_difficulty_reduction and any(mod["acronym"] in ["NF", "EZ", "HT", "DC"] for mod in score["mods"]):
        output_function("Your current settings do not allow difficulty reduction mods.")
        return
    if ctx.minimum_grade:
        grade = calculate_grade(score)
        grades = ["X", "S", "A", "B", "C", "D"]
        if grades.index(grade) >= ctx.minimum_grade:
            required_grade = "SS" if grades[ctx.minimum_grade - 1] == "X" else grades[ctx.minimum_grade - 1]
            output_function(
                f"You did not get a high enough grade. You need atleast a"
                f"{'n' if required_grade == 'A' else ''} {required_grade} Rank"
            )
            return

    played_song_id = score["beatmapset"]["id"]
    song = next((song for song_id, song in ctx.songs.items() if song_id == played_song_id), None)

    if not song:
        return

    output_function(f"Play Matches {song.full_name}")

    # check for the correct diff
    if not ctx.is_difficulty_allowed(song, score["beatmap_id"]):
        output_function("The incorrect difficulty was played")
        output_function(f"The correct difficulty(ies) is: {ctx.permitted_difficulties[song.id]}")
        return

    # check for converts
    if ctx.disallow_converts:
        # Find the diff that was played
        played_difficulty = song.get_beatmap(score["beatmap_id"])

        # Only Standard maps can be converted
        if score["ruleset_id"] != 0 and played_difficulty and played_difficulty.mode == "osu":
            output_function("Your settings do not allow converts")
            return

    if not ctx.is_song_unlocked(song):
        output_function("You don't have this song unlocked")
        return

    if song.id == ctx.victory_song_id:
        if not ctx.can_play_victory_song():
            output_function("You don't have enough performance points")
            return

        message = [{"cmd": "StatusUpdate", "status": ClientStatus.CLIENT_GOAL}]
        asyncio.create_task(ctx.send_msgs(message))
        return

    locations = []
    for i in range(2):
        location_id = ctx.song_locations[song.get_location_name(i)]
        if location_id in ctx.missing_locations:
            locations.append(int(location_id))

    if locations:
        message = [{"cmd": "LocationChecks", "locations": locations}]
        task = asyncio.create_task(ctx.send_msgs(message))
        if ctx.auto_download:
            asyncio.create_task(download_next_beatmapset(ctx, task, output_function))


async def get_last_scores(ctx: OsuContext, mode="", output_function=print):
    # FIXME: Not checking for scores until connected to avoid ignoring scores
    # This should instead put those into some kind of pending list, probably,
    # or check against all songs (rather than just the generated ones)
    if not ctx.songs:
        output_function("Slot data for this world hasn't been received yet")
        return

    # Make URl for the request
    try:
        request = f"https://osu.ppy.sh/api/v2/users/{os.environ['PLAYER_ID']}/scores/recent?include_fails=1&limit=10"
    except KeyError:
        output_function("Set a Player ID")
        return
    # Add Mode to request, otherwise it will use the user's default
    if mode:
        request += f"&mode={mode}"
    if not ctx.token:
        await ctx.get_token(output_function)
        if not ctx.token:
            return
    headers = {
        "Accept": "application/json",
        "Content-Type": "application/json",
        "Authorization": f"Bearer {ctx.token}",
        "x-api-version": "20240529",
    }
    async with ctx.http_session.get(request, headers=headers) as scores:
        try:
            score_list = await scores.json()
            print(score_list, "a")
            print(scores)
        except (KeyError, IndexError):
            await ctx.get_token(output_function)
            output_function("Error Retrieving plays, Check your Client id, Client Secret, and player id.")
            return
    if not score_list:
        output_function("No Plays Found. Check the Gamemode")
        return
    found = False
    for score in score_list:
        if score["ended_at"] in ctx.last_scores:
            if not found:
                output_function("No New Plays Found.")
            return
        found = True
        ctx.last_scores.append(score["ended_at"])
        if len(ctx.last_scores) > 100:
            ctx.last_scores.pop(0)
        check_location(ctx, score, output_function)


# calculates the grade of a score with the ability to differentiate between stable and lazer scores
def calculate_grade(score):
    acc = float(score["accuracy"])
    gamemode = int(score["ruleset_id"])

    # if the score has 100% accuracy, then it is an SS no matter gamemode or grading system
    # skip the rest of the checks
    if acc == 1:
        return "X"

    # if this score has the Classic mod enabled or if it has a legacy id, then we assume it is a stable score
    if any(mod["acronym"] == "CL" for mod in score["mods"]) or score["legacy_score_id"]:
        if gamemode == 0 or gamemode == 1:  # osu!standard or osu!taiko
            # for some reason these statistics are missing if you get 0 of any of these,
            # so we have to check if they exist before we try to get them
            miss_count = int(score["statistics"].get("miss", 0) or 0)
            num_300s = int(score["statistics"].get("great", 0) or 0)
            num_100s = int(score["statistics"].get("ok", 0) or 0)
            num_50s = int(score["statistics"].get("meh", 0) or 0)
            total_judgements = miss_count + num_300s + num_100s + num_50s
            percent_300s = num_300s / total_judgements
            precent_50s = num_50s / (num_300s + num_100s + num_50s)
            if miss_count == 0:
                if percent_300s > 0.9 and precent_50s <= 0.01:
                    return "S"
                if percent_300s > 0.8:
                    return "A"
                if percent_300s > 0.7:
                    return "B"
                if percent_300s > 0.6:
                    return "C"
                return "D"
            if percent_300s > 0.9:
                return "A"
            if percent_300s > 0.8:
                return "B"
            if percent_300s > 0.6:
                return "C"
            return "D"
        if gamemode == 2:  # osu!catch
            if acc > 0.98:
                return "S"
            if acc > 0.94:
                return "A"
            if acc > 0.9:
                return "B"
            if acc > 0.85:
                return "C"
            return "D"
        if gamemode == 3:  # osu!mania
            # Mania's Accuracy with classic mod is different from stable, so we have to do it manually
            mania_total = 300 * score["statistics"].get("perfect", 0)  # Perfects are worth 305 with classic mod
            mania_total += 300 * score["statistics"].get("great", 0)
            mania_total += 200 * score["statistics"].get("good", 0)
            mania_total += 100 * score["statistics"].get("ok", 0)
            mania_total += 50 * score["statistics"].get("meh", 0)
            mania_total += 0 * score["statistics"].get("miss", 0)
            mania_acc = mania_total / (sum(score["statistics"].values()) * 300)  # This is also out of 305 on laser
            if mania_acc > 0.95:
                return "S"
            if mania_acc > 0.9:
                return "A"
            if mania_acc > 0.8:
                return "B"
            if mania_acc > 0.7:
                return "C"
            return "D"

    # if it is a lazer score, then the API did all the work for us
    return score["rank"].replace("XH", "X").replace("SH", "S")  # remove hidden and flashlight from the grade

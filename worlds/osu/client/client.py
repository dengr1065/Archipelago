from __future__ import annotations

import asyncio

import ModuleUpdate
import Utils
from CommonClient import get_base_parser, gui_enabled, server_loop

from . import api
from .context import OsuContext

ModuleUpdate.update()

if __name__ == "__main__":
    Utils.init_logging("osu!Client", exception_logger="Client")


async def game_watcher(ctx: OsuContext):
    count = 0
    while not ctx.exit_event.is_set():
        if count >= 30:
            for mode in ctx.auto_modes:
                await api.get_last_scores(ctx, mode, print)
                await asyncio.sleep(1)
            count = 0
        count += 1
        await asyncio.sleep(0.1)


def main():
    async def _main(args):
        ctx = OsuContext(args.connect, args.password)
        ctx.server_task = asyncio.create_task(server_loop(ctx), name="server loop")
        if gui_enabled:
            ctx.run_gui()
        ctx.run_cli()
        progression_watcher = asyncio.create_task(game_watcher(ctx), name="osu!ProgressionWatcher")

        await ctx.exit_event.wait()
        ctx.server_address = None

        await progression_watcher

        await ctx.shutdown()

    import colorama

    parser = get_base_parser(description="osu! Client, for text interfacing.")

    args, rest = parser.parse_known_args()
    colorama.init()
    asyncio.run(_main(args))
    colorama.deinit()


if __name__ == "__main__":
    main()
